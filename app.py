from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click
import requests
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for

ROOT = Path(__file__).resolve().parent
INSTANCE_DIR = ROOT / "instance"
INSTANCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(INSTANCE_DIR, 0o700)
DB_PATH = Path(os.environ.get("LAWWEB_DB_PATH", str(INSTANCE_DIR / "lawweb.sqlite3"))).expanduser()
DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(DB_PATH.parent, 0o700)

SESSION_HOURS = max(1, min(24, int(os.environ.get("LAWWEB_SESSION_HOURS", "8"))))
IDLE_MINUTES = max(5, min(240, int(os.environ.get("LAWWEB_IDLE_MINUTES", "30"))))
LOGIN_WINDOW = 900
ACCOUNT_LIMIT = 5
IP_LIMIT = 20
MAX_PASSWORD = 1024
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")
PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def load_secret_key() -> str:
    configured = os.environ.get("LAWWEB_SECRET_KEY", "")
    if configured:
        if len(configured) < 64:
            raise RuntimeError("LAWWEB_SECRET_KEYは64文字以上にしてください。")
        return configured
    path = INSTANCE_DIR / "secret.key"
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(64)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(value + "\n")
        except FileExistsError:
            value = path.read_text(encoding="ascii").strip()
        os.chmod(path, 0o600)
    if len(value) < 64:
        raise RuntimeError("署名鍵ファイルが不正です。")
    return value


def connect_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_database() -> None:
    old_umask = os.umask(0o077)
    try:
        with closing(connect_db()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    sid_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_limits (
                    key_hash TEXT PRIMARY KEY,
                    window_started_at INTEGER NOT NULL,
                    failures INTEGER NOT NULL,
                    blocked_until INTEGER NOT NULL DEFAULT 0
                );
            """)
        if DB_PATH.exists():
            os.chmod(DB_PATH, 0o600)
    finally:
        os.umask(old_umask)


def normalize_username(value: str) -> str:
    name = (value or "").strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("ユーザー名は英数字で始まる3〜32文字（英数字、_ . -）にしてください。")
    return name


def validate_password(password: str) -> None:
    if len(password) < 14:
        raise ValueError("パスワードは14文字以上にしてください。")
    if len(password) > MAX_PASSWORD:
        raise ValueError("パスワードが長すぎます。")


def create_user_record(username: str, password: str) -> None:
    name = normalize_username(username)
    validate_password(password)
    with closing(connect_db()) as db:
        try:
            db.execute(
                "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                (name, PASSWORDS.hash(password), int(time.time())),
            )
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ValueError("そのユーザー名は既に使用されています。") from exc


def user_count() -> int:
    with closing(connect_db()) as db:
        return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def rotate_password(username: str, password: str) -> None:
    name = normalize_username(username)
    validate_password(password)
    with closing(connect_db()) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
        if row is None:
            db.rollback()
            raise ValueError("ユーザーが見つかりません。")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (PASSWORDS.hash(password), row["id"]))
        db.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],))
        db.commit()


def password_prompt() -> str:
    value = click.prompt("パスワード", hide_input=True, confirmation_prompt=True)
    try:
        validate_password(value)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def keyed_digest(value: str) -> str:
    return hmac.new(app.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def login_keys(username: str) -> list[tuple[str, int]]:
    remote = request.remote_addr or "unknown"
    try:
        remote = str(ipaddress.ip_address(remote))
    except ValueError:
        remote = "invalid"
    return [(keyed_digest("user:" + username), ACCOUNT_LIMIT),
            (keyed_digest("ip:" + remote), IP_LIMIT)]


def is_limited(keys: list[tuple[str, int]], now: int) -> bool:
    with closing(connect_db()) as db:
        for key, _ in keys:
            row = db.execute("SELECT blocked_until FROM login_limits WHERE key_hash=?", (key,)).fetchone()
            if row and int(row["blocked_until"]) > now:
                return True
    return False


def record_failure(keys: list[tuple[str, int]], now: int) -> None:
    with closing(connect_db()) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM login_limits WHERE window_started_at < ?", (now - 86400,))
        for key, limit in keys:
            row = db.execute(
                "SELECT window_started_at,failures FROM login_limits WHERE key_hash=?", (key,)
            ).fetchone()
            if row is None or now - int(row["window_started_at"]) >= LOGIN_WINDOW:
                started, failures = now, 1
            else:
                started, failures = int(row["window_started_at"]), int(row["failures"]) + 1
            blocked = now + LOGIN_WINDOW if failures >= limit else 0
            db.execute("""
                INSERT INTO login_limits(key_hash,window_started_at,failures,blocked_until)
                VALUES(?,?,?,?)
                ON CONFLICT(key_hash) DO UPDATE SET
                window_started_at=excluded.window_started_at,
                failures=excluded.failures,
                blocked_until=excluded.blocked_until
            """, (key, started, failures, blocked))
        db.commit()


def clear_failures(keys: list[tuple[str, int]]) -> None:
    with closing(connect_db()) as db:
        db.executemany("DELETE FROM login_limits WHERE key_hash=?", [(key,) for key, _ in keys])
        db.commit()


def csrf_token() -> str:
    value = session.get("_csrf")
    if not isinstance(value, str) or len(value) < 32:
        value = secrets.token_urlsafe(32)
        session["_csrf"] = value
    return value


def verify_csrf() -> bool:
    given = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not isinstance(given, str) or not isinstance(expected, str) or not expected:
        return False
    if not secrets.compare_digest(given, expected):
        return False
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.scheme or parsed.netloc.lower() != request.host.lower():
            return False
    return True


def authenticated_user() -> dict[str, Any] | None:
    raw_sid = session.get("_sid")
    if not isinstance(raw_sid, str) or len(raw_sid) < 32:
        return None
    sid_hash = digest(raw_sid)
    now = int(time.time())
    idle_cutoff = now - IDLE_MINUTES * 60
    with closing(connect_db()) as db:
        row = db.execute("""
            SELECT s.user_id,s.last_seen_at,s.expires_at,u.username,u.disabled
            FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.sid_hash=?
        """, (sid_hash,)).fetchone()
        if (row is None or int(row["expires_at"]) <= now or
                int(row["last_seen_at"]) <= idle_cutoff or int(row["disabled"]) != 0):
            db.execute("DELETE FROM auth_sessions WHERE sid_hash=?", (sid_hash,))
            db.commit()
            session.clear()
            return None
        db.execute("UPDATE auth_sessions SET last_seen_at=? WHERE sid_hash=?", (now, sid_hash))
        db.commit()
        return {"id": int(row["user_id"]), "username": str(row["username"])}


def require_loopback() -> None:
    host = (urlsplit("//" + request.host).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        abort(403)
    try:
        if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
            abort(403)
    except ValueError:
        abort(403)


app = Flask(__name__, instance_path=str(INSTANCE_DIR))
app.secret_key = load_secret_key()
app.config.update(
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=1024 * 1024,
    SESSION_COOKIE_NAME="lawweb_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_PATH="/",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    SESSION_REFRESH_EACH_REQUEST=False,
    TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"],
)

API_ORIGIN = os.environ.get("LAWGIT_API_URL", "http://127.0.0.1:8787").rstrip("/")
API_HOST = (urlsplit(API_ORIGIN).hostname or "").lower()
if API_HOST not in {"127.0.0.1", "localhost", "::1"} or urlsplit(API_ORIGIN).scheme != "http":
    raise RuntimeError("LAWGIT_API_URLはローカルHTTP APIを指定してください。")
API_ROOT = API_ORIGIN + "/api/v1"
API_TOKEN = os.environ.get("LAWGIT_TOKEN", "")
API_TIMEOUT = min(30.0, max(1.0, float(os.environ.get("LAWGIT_TIMEOUT", "10"))))
init_database()


@app.before_request
def security_gate():
    require_loopback()
    if request.path.startswith("/static/"):
        return None

    if request.path == "/login":
        if request.method not in {"GET", "HEAD", "POST"}:
            abort(405)
        if request.method == "POST" and not verify_csrf():
            abort(403)
        if request.method in {"GET", "HEAD"} and authenticated_user():
            return redirect(url_for("index"))
        return None

    if request.path == "/logout":
        if request.method != "POST":
            abort(405)
        user = authenticated_user()
        if user is None:
            return redirect(url_for("login"))
        g.user = user
        if not verify_csrf():
            abort(403)
        return None

    user = authenticated_user()
    if user is None:
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "ログインが必要です。"}), 401
        return redirect(url_for("login"))
    g.user = user
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        abort(405)


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.context_processor
def template_context():
    return {"csrf_token": csrf_token(), "current_user": getattr(g, "user", None)}


@app.get("/login")
def login():
    message = ("まだアカウントがありません。ターミナルで flask --app app create-user を実行してください。"
               if user_count() == 0 else "")
    return render_template("login.html", error=None, setup_message=message)


@app.post("/login")
def login_post():
    if authenticated_user():
        return redirect(url_for("index"))
    raw_name = request.form.get("username", "")
    password = request.form.get("password", "")
    try:
        username = normalize_username(raw_name)
    except ValueError:
        username = "invalid-user"

    now = int(time.time())
    keys = login_keys(username)
    if is_limited(keys, now):
        return render_template("login.html",
            error="ログイン試行回数が上限に達しました。15分後に再試行してください。",
            setup_message=""), 429

    with closing(connect_db()) as db:
        user = db.execute(
            "SELECT id,username,password_hash,disabled FROM users WHERE username=?", (username,)
        ).fetchone()

    stored_hash = str(user["password_hash"]) if user else DUMMY_HASH
    valid = False
    try:
        valid = PASSWORDS.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        valid = False

    if user is None or int(user["disabled"]) != 0 or not valid or len(password) > MAX_PASSWORD:
        record_failure(keys, now)
        return render_template("login.html",
            error="ユーザー名またはパスワードが正しくありません。",
            setup_message=""), 401

    clear_failures(keys)
    if PASSWORDS.check_needs_rehash(stored_hash):
        with closing(connect_db()) as db:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (PASSWORDS.hash(password), user["id"]))
            db.commit()

    raw_sid = secrets.token_urlsafe(48)
    now = int(time.time())
    with closing(connect_db()) as db:
        db.execute(
            "INSERT INTO auth_sessions VALUES(?,?,?,?,?)",
            (digest(raw_sid), user["id"], now, now, now + SESSION_HOURS * 3600),
        )
        db.commit()

    session.clear()
    session.permanent = True
    session["_sid"] = raw_sid
    session["_csrf"] = secrets.token_urlsafe(32)
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    raw_sid = session.get("_sid")
    if isinstance(raw_sid, str):
        with closing(connect_db()) as db:
            db.execute("DELETE FROM auth_sessions WHERE sid_hash=?", (digest(raw_sid),))
            db.commit()
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    return render_template("index.html")


def lawgit_request(path: str, params: dict[str, Any] | None = None):
    headers = {"Accept": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = "Bearer " + API_TOKEN
    try:
        response = requests.get(
            API_ROOT + "/" + path.lstrip("/"), params=params, headers=headers,
            timeout=(2.0, API_TIMEOUT), allow_redirects=False
        )
        body = response.json()
    except (requests.RequestException, ValueError):
        return None, {"status": 503, "message": "LawGit APIに接続できません。"}
    if response.is_redirect:
        return None, {"status": 502, "message": "LawGit APIのリダイレクトを拒否しました。"}
    if response.status_code >= 400 or (isinstance(body, dict) and body.get("ok") is False):
        message = body.get("error", "LawGit APIでエラーが発生しました。") if isinstance(body, dict) else "LawGit APIでエラーが発生しました。"
        return None, {"status": response.status_code if response.status_code >= 400 else 400, "message": message}
    return (body.get("result") if isinstance(body, dict) and "result" in body else body), None


def api_response(path: str, params=None):
    result, error = lawgit_request(path, params)
    if error:
        return jsonify({"ok": False, "error": error["message"]}), error["status"]
    return jsonify({"ok": True, "result": result})


@app.get("/api/overview")
def overview():
    ping, error = lawgit_request("ping")
    if error:
        return jsonify({"ok": True, "result": {"connected": False, "error": error["message"]}})
    laws, laws_err = lawgit_request("laws")
    rules, rules_err = lawgit_request("rules")
    categories, categories_err = lawgit_request("categories")
    status, status_err = lawgit_request("git/status")
    log, log_err = lawgit_request("git/log", {"limit": 6})
    errors = {name: err["message"] for name, err in (
        ("laws", laws_err), ("rules", rules_err), ("categories", categories_err),
        ("git_status", status_err), ("log", log_err)
    ) if err}
    return jsonify({"ok": True, "result": {
        "connected": True, "ping": ping,
        "laws": laws or {"laws": []}, "rules": rules or {"rules": []},
        "categories": categories or {"laws": [], "rules": []},
        "git_status": status, "log": log or {"commits": []}, "errors": errors
    }})


@app.get("/api/ping")
def ping():
    return api_response("ping")


@app.get("/api/categories")
def categories():
    return api_response("categories")


@app.get("/api/laws")
def laws():
    params = {k: request.args.get(k) for k in ("category", "type") if request.args.get(k)}
    return api_response("laws", params)


@app.get("/api/laws/<uuid>")
def get_law(uuid: str):
    return api_response("laws/" + uuid)


@app.get("/api/laws/<uuid>/history")
def law_history(uuid: str):
    return api_response("laws/" + uuid + "/history", {"limit": request.args.get("limit", "100")})


@app.get("/api/laws/<uuid>/revisions/<rev>")
def law_revision(uuid: str, rev: str):
    return api_response("laws/" + uuid + "/revisions/" + rev)


@app.get("/api/rules")
def rules():
    params = {k: request.args.get(k) for k in ("category", "query") if request.args.get(k)}
    return api_response("rules", params)


@app.get("/api/rules/<uuid>")
def get_rule(uuid: str):
    return api_response("rules/" + uuid)


@app.get("/api/rules/<uuid>/history")
def rule_history(uuid: str):
    return api_response("rules/" + uuid + "/history", {"limit": request.args.get("limit", "100")})


@app.get("/api/rules/<uuid>/revisions/<rev>")
def rule_revision(uuid: str, rev: str):
    return api_response("rules/" + uuid + "/revisions/" + rev)


@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "検索語を入力してください。"}), 400
    return api_response("search", {"q": query, "limit": request.args.get("limit", "50")})


@app.get("/api/changes")
def changes():
    params = {k: request.args.get(k) for k in ("since", "limit") if request.args.get(k)}
    return api_response("changes", params)


@app.get("/api/git/status")
def git_status():
    return api_response("git/status")


@app.get("/api/git/log")
def git_log():
    return api_response("git/log", {"limit": request.args.get("limit", "30")})


@app.get("/api/validate")
def validate():
    return api_response("validate")


@app.errorhandler(403)
def forbidden(_error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "この操作は許可されていません。"}), 403
    return render_template("login.html", error="リクエストを検証できませんでした。ページを再読み込みしてください。", setup_message=""), 403


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "LawWeb API endpoint not found"}), 404
    return render_template("index.html"), 404


@app.cli.command("create-user")
@click.option("--username", prompt="ユーザー名")
def create_user_command(username: str):
    try:
        create_user_record(username, password_prompt())
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("アカウントを作成しました。")


@app.cli.command("change-password")
@click.option("--username", prompt="ユーザー名")
def change_password_command(username: str):
    try:
        rotate_password(username, password_prompt())
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("パスワードを変更し、既存セッションをすべて失効させました。")


@app.cli.command("revoke-sessions")
@click.option("--username", default="")
def revoke_sessions_command(username: str):
    with closing(connect_db()) as db:
        if username:
            name = normalize_username(username)
            user = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            if user is None:
                raise click.ClickException("ユーザーが見つかりません。")
            db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user["id"],))
        else:
            db.execute("DELETE FROM auth_sessions")
        db.commit()
    click.echo("セッションを失効させました。")


if os.environ.get("WEB_HOST", "127.0.0.1") != "127.0.0.1":
    raise RuntimeError("LawWebは安全のため127.0.0.1でのみ待ち受けます。")
