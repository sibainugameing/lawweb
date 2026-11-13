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
from law_markdown import LawMarkdownError, law_markdown_diff, law_xml_to_markdown

ROOT = Path(__file__).resolve().parent
INSTANCE_DIR = ROOT / "instance"
INSTANCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(INSTANCE_DIR, 0o700)
DB_PATH = INSTANCE_DIR / "lawweb.sqlite3"
DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(DB_PATH.parent, 0o700)

SESSION_HOURS = max(1, min(24, int(os.environ.get("LAWWEB_SESSION_HOURS", "8"))))
IDLE_MINUTES = max(5, min(240, int(os.environ.get("LAWWEB_IDLE_MINUTES", "30"))))
LOGIN_WINDOW = 900
ACCOUNT_LIMIT = 5
IP_LIMIT = 20
REGISTRATION_LIMIT = 8
MAX_PASSWORD = 1024
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")
LAW_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REVISION_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
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
            now = int(time.time())
            db.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ? OR last_seen_at <= ?",
                (now, now - IDLE_MINUTES * 60),
            )
            db.commit()
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


def same_request_origin(value: str, *, allow_path: bool = False) -> bool:
    """Validate the Origin/Referer host, scheme, and port against this request."""
    try:
        parsed = urlsplit(value)
        target = urlsplit("//" + request.host)
        if parsed.scheme.lower() != request.scheme.lower() or not parsed.hostname or not target.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        if not allow_path and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return False

        origin_host = parsed.hostname.lower().rstrip(".")
        target_host = target.hostname.lower().rstrip(".")
        loopback_names = {"localhost", "127.0.0.1", "::1"}
        hosts_match = origin_host == target_host or (
            origin_host in loopback_names and target_host in loopback_names
        )
        default_port = 443 if request.scheme.lower() == "https" else 80
        origin_port = parsed.port or default_port
        target_port = target.port or default_port
        return hosts_match and origin_port == target_port
    except ValueError:
        return False


def verify_csrf() -> bool:
    given = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not isinstance(given, str) or not isinstance(expected, str) or not expected:
        g.csrf_failure_reason = "missing_token"
        return False
    if not secrets.compare_digest(given, expected):
        g.csrf_failure_reason = "token_mismatch"
        return False

    fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    if fetch_site == "cross-site":
        g.csrf_failure_reason = "cross_site"
        return False

    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")

    # Some LAN browsers / embedded views send Origin: null and omit Fetch Metadata.
    # This is accepted only after the session-bound synchronizer token has matched,
    # the request has passed require_local_network(), and the request is not marked
    # cross-site. If a Referer is present, it must still match this exact origin.
    if origin and origin.strip().lower() == "null":
        if fetch_site not in {"", "same-origin", "same-site"}:
            g.csrf_failure_reason = "opaque_origin_cross_site"
            return False
        if referer and not same_request_origin(referer, allow_path=True):
            g.csrf_failure_reason = "origin_mismatch"
            return False
    else:
        source = origin if origin else referer
        if source and not same_request_origin(source, allow_path=not bool(origin)):
            g.csrf_failure_reason = "origin_mismatch"
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


PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
PRIVATE_IPV6_NETWORKS = (ipaddress.ip_network("fc00::/7"),)


def is_local_network_ip(value: str) -> bool:
    """Allow loopback and RFC1918/IPv6 ULA addresses only."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback:
        return True
    networks = PRIVATE_IPV4_NETWORKS if address.version == 4 else PRIVATE_IPV6_NETWORKS
    return any(address in network for network in networks)


def is_local_network_host(value: str) -> bool:
    """Allow loopback/private IP hosts and valid multicast-DNS .local names."""
    host = (value or "").lower().rstrip(".")
    if host == "localhost" or is_local_network_ip(host):
        return True
    if not host.endswith(".local"):
        return False
    labels = host.split(".")
    return len(labels) >= 2 and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    )


def require_local_network() -> None:
    host = (urlsplit("//" + request.host).hostname or "").lower()
    if not is_local_network_host(host):
        g.security_failure_reason = "host_not_local_network"
        abort(403)
    if not is_local_network_ip(request.remote_addr or ""):
        g.security_failure_reason = "remote_not_local_network"
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
    TRUSTED_HOSTS=None,
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
    require_local_network()
    if request.path.startswith("/static/"):
        return None

    if request.path in {"/login", "/register"}:
        if request.method not in {"GET", "HEAD", "POST"}:
            abort(405)
        if request.method == "POST" and not verify_csrf():
            app.logger.warning(
                "CSRF rejected path=%s reason=%s host=%s origin=%s referer=%s fetch_site=%s",
                request.path, getattr(g, "csrf_failure_reason", "unknown"),
                request.host, request.headers.get("Origin", ""),
                request.headers.get("Referer", ""),
                request.headers.get("Sec-Fetch-Site", ""),
            )
            abort(403)
        if request.method in {"GET", "HEAD"} and authenticated_user():
            return redirect(url_for("index"))
        return None

    if request.path == "/account":
        if request.method not in {"GET", "HEAD"}:
            abort(405)
        user = authenticated_user()
        if user is None:
            return redirect(url_for("login"))
        g.user = user
        return None

    if request.path in {"/account/password", "/account/sessions/revoke", "/account/delete"}:
        if request.method != "POST":
            abort(405)
        user = authenticated_user()
        if user is None:
            return redirect(url_for("login"))
        g.user = user
        if not verify_csrf():
            app.logger.warning(
                "CSRF rejected path=%s reason=%s host=%s origin=%s referer=%s fetch_site=%s",
                request.path, getattr(g, "csrf_failure_reason", "unknown"),
                request.host, request.headers.get("Origin", ""),
                request.headers.get("Referer", ""),
                request.headers.get("Sec-Fetch-Site", ""),
            )
            abort(403)
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

    # Anonymous users may read public law search and law text only; all other APIs stay private.
    if request.path == "/" or request.path.startswith("/api/public/"):
        if request.method not in {"GET", "HEAD"}:
            abort(405)
        public_page_user = authenticated_user()
        if public_page_user is not None:
            g.user = public_page_user
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
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-DNS-Prefetch-Control"] = "off"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.context_processor
def template_context():
    return {"csrf_token": csrf_token(), "current_user": getattr(g, "user", None)}


def begin_user_session(user_id: int) -> None:
    raw_sid = secrets.token_urlsafe(48)
    now = int(time.time())
    with closing(connect_db()) as db:
        db.execute(
            "INSERT INTO auth_sessions(sid_hash,user_id,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?)",
            (digest(raw_sid), user_id, now, now, now + SESSION_HOURS * 3600),
        )
        db.commit()

    # Clear any pre-authentication session, then issue a fresh authenticated session.
    session.clear()
    session.permanent = True
    session["_sid"] = raw_sid
    session["_csrf"] = secrets.token_urlsafe(32)


def render_login(error: str | None = None, status: int = 200):
    return render_template("login.html", error=error, setup_message=""), status


@app.get("/login")
def login():
    return render_login()


@app.post("/login")
def login_post():
    existing_user = authenticated_user()
    if existing_user:
        return redirect(url_for("index"))

    raw_name = request.form.get("username", "")
    password = request.form.get("password", "")
    try:
        username = normalize_username(raw_name)
    except ValueError:
        # Keep the work factor similar for malformed and unknown account names.
        username = "invalid-" + secrets.token_hex(12)

    now = int(time.time())
    keys = login_keys(username)
    if is_limited(keys, now):
        return render_login("ログイン試行回数が上限に達しました。15分後に再試行してください。", 429)

    with closing(connect_db()) as db:
        user = db.execute(
            "SELECT id,username,password_hash,disabled FROM users WHERE username=?", (username,)
        ).fetchone()

    too_long = len(password) > MAX_PASSWORD
    verify_hash = str(user["password_hash"]) if user and not too_long else DUMMY_HASH
    password_ok = False
    try:
        password_ok = PASSWORDS.verify(verify_hash, password[:MAX_PASSWORD])
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        password_ok = False

    if user is None or int(user["disabled"]) != 0 or not password_ok or too_long:
        record_failure(keys, now)
        return render_login("ユーザー名またはパスワードが正しくありません。", 401)

    clear_failures(keys)
    if PASSWORDS.check_needs_rehash(verify_hash):
        with closing(connect_db()) as db:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (PASSWORDS.hash(password), user["id"]))
            db.commit()

    begin_user_session(int(user["id"]))
    return redirect(url_for("index"))


@app.get("/register")
def register():
    return render_template("register.html", error=None, username="")


@app.post("/register")
def register_post():
    if authenticated_user():
        return redirect(url_for("index"))

    remote = request.remote_addr or "unknown"
    try:
        remote = str(ipaddress.ip_address(remote))
    except ValueError:
        remote = "invalid"
    registration_key = [(keyed_digest("registration-ip:" + remote), REGISTRATION_LIMIT)]
    now = int(time.time())

    if is_limited(registration_key, now):
        return render_template(
            "register.html",
            error="登録試行回数が上限に達しました。15分後に再試行してください。",
            username="",
        ), 429

    # Count every registration attempt, including invalid submissions.
    record_failure(registration_key, now)

    username_input = request.form.get("username", "")
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    try:
        username = normalize_username(username_input)
        validate_password(password)
        if password != password_confirm:
            raise ValueError("確認用パスワードが一致しません。")
        create_user_record(username, password)
    except ValueError as exc:
        return render_template(
            "register.html",
            error=str(exc),
            username=username_input[:32],
        ), 400

    with closing(connect_db()) as db:
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if user is None:
        return render_login("アカウントを作成できませんでした。", 500)

    begin_user_session(int(user["id"]))
    return redirect(url_for("index"))


def render_account(error: str | None = None, success: str | None = None, status: int = 200):
    with closing(connect_db()) as db:
        row = db.execute(
            "SELECT username,created_at FROM users WHERE id=?", (g.user["id"],)
        ).fetchone()
    if row is None:
        return redirect(url_for("login"))
    created = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(row["created_at"])))
    return render_template(
        "account.html", username=str(row["username"]),
        created_at=created, error=error, success=success,
    ), status


@app.get("/account")
def account_page():
    return render_account(success=request.args.get("status"))


@app.post("/account/password")
def account_change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm = request.form.get("password_confirm", "")
    with closing(connect_db()) as db:
        row = db.execute(
            "SELECT password_hash FROM users WHERE id=?", (g.user["id"],)
        ).fetchone()
    try:
        if row is None or not PASSWORDS.verify(str(row["password_hash"]), current_password):
            raise ValueError("現在のパスワードが正しくありません。")
        validate_password(new_password)
        if new_password != confirm:
            raise ValueError("新しいパスワードが一致しません。")
    except (ValueError, VerifyMismatchError, VerificationError, InvalidHashError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "現在のパスワードが正しくありません。"
        return render_account(error=message, status=400)

    with closing(connect_db()) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (PASSWORDS.hash(new_password), g.user["id"]),
        )
        db.execute("DELETE FROM auth_sessions WHERE user_id=?", (g.user["id"],))
        db.commit()
    begin_user_session(int(g.user["id"]))
    return redirect(url_for("account_page", status="password-updated"))


@app.post("/account/sessions/revoke")
def account_revoke_other_sessions():
    raw_sid = session.get("_sid")
    if not isinstance(raw_sid, str):
        return redirect(url_for("login"))
    with closing(connect_db()) as db:
        db.execute(
            "DELETE FROM auth_sessions WHERE user_id=? AND sid_hash<>?",
            (g.user["id"], digest(raw_sid)),
        )
        db.commit()
    return redirect(url_for("account_page", status="sessions-revoked"))


@app.post("/account/delete")
def account_delete():
    password = request.form.get("password", "")
    confirmation = request.form.get("confirmation", "")
    with closing(connect_db()) as db:
        row = db.execute(
            "SELECT password_hash FROM users WHERE id=?", (g.user["id"],)
        ).fetchone()
    try:
        if row is None or not PASSWORDS.verify(str(row["password_hash"]), password):
            raise ValueError("パスワードが正しくありません。")
    except (ValueError, VerifyMismatchError, VerificationError, InvalidHashError):
        return render_account(error="パスワードが正しくありません。", status=400)
    if confirmation != "DELETE":
        return render_account(error="確認欄に DELETE と入力してください。", status=400)

    with closing(connect_db()) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM users WHERE id=?", (g.user["id"],))
        db.commit()
    session.clear()
    return redirect(url_for("register", deleted="1"))


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


@app.get("/api/public/overview")
def public_overview():
    ping, error = lawgit_request("ping")
    if error:
        return jsonify({"ok": True, "result": {"connected": False, "error": error["message"]}})
    laws, laws_error = lawgit_request("laws")
    categories, categories_error = lawgit_request("categories")
    if laws_error:
        return jsonify({"ok": False, "error": laws_error["message"]}), laws_error["status"]
    law_items = (laws or {}).get("laws", [])
    public_laws = {"laws": [public_law_fields(item) for item in law_items]}
    return jsonify({"ok": True, "result": {
        "connected": True,
        "ping": {"name": ping.get("name"), "version": ping.get("version")},
        "laws": public_laws,
        "categories": {"laws": (categories or {}).get("laws", [])},
        "errors": {"categories": categories_error["message"]} if categories_error else {},
    }})


def public_law_fields(law: dict[str, Any]) -> dict[str, Any]:
    allowed = ("uuid", "name", "type", "law_number", "e_gov_id", "category")
    return {key: law.get(key) for key in allowed if key in law}


@app.get("/api/public/laws")
def public_laws():
    params = {key: request.args.get(key) for key in ("category", "type") if request.args.get(key)}
    laws, error = lawgit_request("laws", params)
    if error:
        return jsonify({"ok": False, "error": error["message"]}), error["status"]
    result = laws or {}
    return jsonify({"ok": True, "result": {
        "laws": [public_law_fields(item) for item in result.get("laws", [])],
    }})


@app.get("/api/public/laws/<uuid>")
def public_get_law(uuid: str):
    if not valid_law_uuid(uuid):
        return jsonify({"ok": False, "error": "法令UUIDの形式が正しくありません。"}), 400
    result, error = lawgit_request("laws/" + uuid)
    if error:
        return jsonify({"ok": False, "error": error["message"]}), error["status"]

    # Legal text is public. Return its readable Markdown representation, but
    # deliberately omit raw XML, repository paths, versions and Git internals.
    enriched = attach_law_markdown(result or {})
    law = enriched.get("law", {}) if isinstance(enriched, dict) else {}
    public_result = {"law": public_law_fields(law)}
    if isinstance(enriched, dict) and isinstance(enriched.get("markdown"), str):
        public_result["markdown"] = enriched["markdown"]
    elif isinstance(enriched, dict) and enriched.get("markdown_error"):
        public_result["markdown_error"] = enriched["markdown_error"]
    return jsonify({"ok": True, "result": public_result})


@app.get("/api/public/search")
def public_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "検索語を入力してください。"}), 400
    if len(query) > 120:
        return jsonify({"ok": False, "error": "検索語は120文字以内にしてください。"}), 400
    try:
        limit = int(request.args.get("limit", "30"))
    except ValueError:
        return jsonify({"ok": False, "error": "検索件数が不正です。"}), 400
    limit = min(50, max(1, limit))

    result, error = lawgit_request("search", {"q": query, "limit": limit})
    if error:
        return jsonify({"ok": False, "error": error["message"]}), error["status"]

    # Do not expose user-authored rules or arbitrary fields from search hits.
    hits = []
    for hit in (result or {}).get("hits", []):
        if not isinstance(hit, dict) or hit.get("kind") != "law":
            continue
        uuid = hit.get("uuid", "")
        if not valid_law_uuid(uuid):
            continue
        hits.append({
            "kind": "law",
            "uuid": uuid,
            "name": str(hit.get("name") or "名称未設定")[:300],
            "matched": str(hit.get("matched") or "")[:80],
            "snippet": str(hit.get("snippet") or "")[:1200],
        })
    return jsonify({"ok": True, "result": {
        "query": query,
        "hits": hits,
        "truncated": bool((result or {}).get("truncated", False)),
    }})


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


def attach_law_markdown(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    enriched = dict(result)
    xml_text = enriched.get("xml")
    if isinstance(xml_text, str):
        try:
            enriched["markdown"] = law_xml_to_markdown(xml_text)
        except LawMarkdownError as exc:
            enriched["markdown_error"] = str(exc)
    return enriched


def valid_law_uuid(uuid: str) -> bool:
    return bool(LAW_UUID_RE.fullmatch(uuid))


@app.get("/api/laws/<uuid>")
def get_law(uuid: str):
    if not valid_law_uuid(uuid):
        return jsonify({"ok": False, "error": "法令UUIDの形式が正しくありません。"}), 400
    result, error = lawgit_request("laws/" + uuid)
    if error:
        return jsonify({"ok": False, "error": error["message"]}), error["status"]
    return jsonify({"ok": True, "result": attach_law_markdown(result)})


@app.get("/api/laws/<uuid>/history")
def law_history(uuid: str):
    if not valid_law_uuid(uuid):
        return jsonify({"ok": False, "error": "法令UUIDの形式が正しくありません。"}), 400
    return api_response("laws/" + uuid + "/history", {"limit": request.args.get("limit", "100")})


@app.get("/api/laws/<uuid>/revisions/<rev>")
def law_revision(uuid: str, rev: str):
    if not valid_law_uuid(uuid):
        return jsonify({"ok": False, "error": "法令UUIDの形式が正しくありません。"}), 400
    if not REVISION_RE.fullmatch(rev):
        return jsonify({"ok": False, "error": "コミットIDの形式が正しくありません。"}), 400
    result, error = lawgit_request("laws/" + uuid + "/revisions/" + rev)
    if error:
        return jsonify({"ok": False, "error": error["message"]}), error["status"]
    return jsonify({"ok": True, "result": attach_law_markdown(result)})


@app.get("/api/laws/<uuid>/diff")
def law_revision_diff(uuid: str):
    if not valid_law_uuid(uuid):
        return jsonify({"ok": False, "error": "法令UUIDの形式が正しくありません。"}), 400
    older_rev = request.args.get("older", "").strip()
    newer_rev = request.args.get("newer", "").strip()
    if not REVISION_RE.fullmatch(older_rev) or not REVISION_RE.fullmatch(newer_rev):
        return jsonify({"ok": False, "error": "比較するコミットIDが不正です。"}), 400
    if older_rev == newer_rev:
        return jsonify({"ok": False, "error": "異なる2つの版を選択してください。"}), 400

    older, older_error = lawgit_request("laws/" + uuid + "/revisions/" + older_rev)
    if older_error:
        return jsonify({"ok": False, "error": older_error["message"]}), older_error["status"]
    newer, newer_error = lawgit_request("laws/" + uuid + "/revisions/" + newer_rev)
    if newer_error:
        return jsonify({"ok": False, "error": newer_error["message"]}), newer_error["status"]
    older_xml = older.get("xml") if isinstance(older, dict) else None
    newer_xml = newer.get("xml") if isinstance(newer, dict) else None
    if not isinstance(older_xml, str) or not isinstance(newer_xml, str):
        return jsonify({"ok": False, "error": "比較対象の版からXMLを取得できませんでした。"}), 502
    try:
        diff = law_markdown_diff(
            older_xml, newer_xml,
            older_label="旧版 " + older_rev[:8],
            newer_label="新版 " + newer_rev[:8],
        )
    except LawMarkdownError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422

    additions = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++ "))
    deletions = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("--- "))
    return jsonify({"ok": True, "result": {
        "older": older_rev, "newer": newer_rev,
        "older_subject": older.get("subject", "") if isinstance(older, dict) else "",
        "newer_subject": newer.get("subject", "") if isinstance(newer, dict) else "",
        "diff": diff, "additions": additions, "deletions": deletions,
        "identical": not bool(diff),
    }})


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
    if len(query) > 300:
        return jsonify({"ok": False, "error": "検索語は300文字以内にしてください。"}), 400
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
    reason = getattr(g, "security_failure_reason", "")
    if reason in {"host_not_local_network", "remote_not_local_network"}:
        message = (
            "LawWebは同じPCまたは同じプライベートネットワークからのみアクセスできます。"
            "PCのLAN内IPアドレスまたは .local 名でアクセスしてください。"
            "公開IPアドレスやインターネット経由の接続は許可されていません。"
        )
    else:
        csrf_reason = getattr(g, "csrf_failure_reason", "")
        csrf_messages = {
            "missing_token": (
                "CSRFトークンがフォームまたはセッションCookieから取得できませんでした。"
                "このURLを開き直し、同じホスト名のまま送信してください。"
            ),
            "token_mismatch": (
                "フォームを開いた後にセッションが変わったため、確認に失敗しました。"
                "登録ページを再読み込みして、もう一度入力してください。"
            ),
            "cross_site": (
                "ブラウザーが別サイトからの送信と判定したため拒否しました。"
                "LawWebの登録ページを直接開いてください。"
            ),
            "origin_mismatch": (
                "フォームを開いたURLと送信先のホスト名またはポートが一致しません。"
                "PCのLAN内IPアドレスまたは .local 名で登録ページを開き、同じURLで送信してください。"
            ),
        }
        message = csrf_messages.get(
            csrf_reason,
            "セキュリティ検証に失敗しました。LawWebの登録ページを再読み込みしてください。",
        )
    if request.path == "/register":
        return render_template("register.html", error=message, username=request.form.get("username", "")[:32]), 403
    if request.path.startswith("/account") and getattr(g, "user", None):
        return render_account(error=message, status=403)
    return render_template("login.html", error=message, setup_message=""), 403


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "LawWeb API endpoint not found"}), 404
    if request.path.startswith("/static/"):
        return "Not found", 404
    if getattr(g, "user", None) is None:
        return redirect(url_for("login"))
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


@app.cli.command("reset-accounts")
@click.confirmation_option(prompt="全ユーザー・全セッション・全ログイン制限を削除します。続行しますか？")
def reset_accounts_command():
    """Delete all account and authentication records while preserving configuration and legal data."""
    with closing(connect_db()) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM auth_sessions")
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM login_limits")
        db.commit()
    click.echo("全アカウント・セッション・ログイン制限を削除しました。設定・秘密鍵・法令データは変更していません。")


@app.cli.command("reset-login-limits")
def reset_login_limits_command():
    """Clear account and address login throttles after a local operator verifies the issue."""
    with closing(connect_db()) as db:
        db.execute("DELETE FROM login_limits")
        db.commit()
    click.echo("ログイン試行制限をすべて解除しました。")


WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0").strip()
if WEB_HOST != "0.0.0.0" and not is_local_network_ip(WEB_HOST):
    raise RuntimeError(
        "WEB_HOSTは0.0.0.0、ループバック、またはプライベートネットワークのIPを指定してください。"
    )
