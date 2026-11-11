from __future__ import annotations

import os
from typing import Any

import requests
from flask import Flask, abort, jsonify, render_template, request

app = Flask(__name__)
app.config.update(JSON_SORT_KEYS=False)

API_ORIGIN = os.environ.get("LAWGIT_API_URL", "http://127.0.0.1:8787").rstrip("/")
API_ROOT = API_ORIGIN + "/api/v1"
API_TOKEN = os.environ.get("LAWGIT_TOKEN", "")
API_TIMEOUT = float(os.environ.get("LAWGIT_TIMEOUT", "15"))


def lawgit_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Call LawGit's local HTTP API. The token never leaves this process."""
    headers = {"Accept": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = "Bearer " + API_TOKEN
    if payload is not None:
        headers["Content-Type"] = "application/json"

    try:
        response = requests.request(
            method,
            API_ROOT + "/" + path.lstrip("/"),
            params=params,
            json=payload,
            headers=headers,
            timeout=API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return None, {
            "status": 503,
            "message": "LawGit APIに接続できません。run.shの起動ログと接続設定を確認してください。",
            "detail": str(exc),
        }

    try:
        body = response.json()
    except ValueError:
        return None, {
            "status": response.status_code if response.status_code >= 400 else 502,
            "message": "LawGit APIからJSON以外の応答を受信しました。",
        }

    if response.status_code >= 400 or (isinstance(body, dict) and body.get("ok") is False):
        return None, {
            "status": response.status_code if response.status_code >= 400 else 400,
            "message": body.get("error", "LawGit APIでエラーが発生しました。")
            if isinstance(body, dict)
            else "LawGit APIでエラーが発生しました。",
        }

    if isinstance(body, dict) and "result" in body:
        return body["result"], None
    return body, None


def api_response(method: str, path: str, *, params=None, payload=None):
    result, error = lawgit_request(method, path, params=params, payload=payload)
    if error:
        status = int(error.get("status", 502))
        return jsonify({"ok": False, "error": error.get("message", "API error")}), status
    return jsonify({"ok": True, "result": result})


@app.before_request
def protect_mutations():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    # Browser-side writes must originate from this UI, not a cross-site form.
    if request.headers.get("X-Lawweb-Request") != "1":
        abort(403)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        abort(403)
    if request.method in {"POST", "PUT", "PATCH"} and not request.is_json:
        return jsonify({"ok": False, "error": "Content-Type: application/json が必要です。"}), 415
    return None


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "lawweb"})


@app.get("/api/overview")
def overview():
    ping, error = lawgit_request("GET", "ping")
    if error:
        return jsonify({
            "ok": True,
            "result": {
                "connected": False,
                "error": error.get("message"),
                "details": error.get("detail", ""),
            },
        })

    laws, laws_error = lawgit_request("GET", "laws")
    rules, rules_error = lawgit_request("GET", "rules")
    categories, categories_error = lawgit_request("GET", "categories")
    status, status_error = lawgit_request("GET", "git/status")
    log, log_error = lawgit_request("GET", "git/log", params={"limit": 6})

    return jsonify({
        "ok": True,
        "result": {
            "connected": True,
            "ping": ping,
            "laws": laws or {"laws": [], "errors": []},
            "rules": rules or {"rules": [], "errors": []},
            "categories": categories or {"laws": [], "rules": []},
            "git_status": status,
            "log": log or {"commits": []},
            "errors": {
                name: err.get("message")
                for name, err in (
                    ("laws", laws_error),
                    ("rules", rules_error),
                    ("categories", categories_error),
                    ("git_status", status_error),
                    ("log", log_error),
                )
                if err
            },
        },
    })


@app.get("/api/ping")
def ping():
    return api_response("GET", "ping")


@app.get("/api/categories")
def categories():
    return api_response("GET", "categories")


@app.get("/api/laws")
def laws():
    params = {
        key: request.args.get(key)
        for key in ("category", "type")
        if request.args.get(key)
    }
    return api_response("GET", "laws", params=params)


@app.post("/api/laws")
def create_law():
    return api_response("POST", "laws", payload=request.get_json())


@app.get("/api/laws/<uuid>")
def get_law(uuid: str):
    return api_response("GET", "laws/" + uuid)


@app.get("/api/laws/<uuid>/history")
def law_history(uuid: str):
    params = {"limit": request.args.get("limit", "100")}
    return api_response("GET", "laws/" + uuid + "/history", params=params)


@app.get("/api/laws/<uuid>/revisions/<rev>")
def law_revision(uuid: str, rev: str):
    return api_response("GET", "laws/" + uuid + "/revisions/" + rev)


@app.get("/api/rules")
def rules():
    params = {
        key: request.args.get(key)
        for key in ("category", "query")
        if request.args.get(key)
    }
    return api_response("GET", "rules", params=params)


@app.post("/api/rules")
def create_rule():
    return api_response("POST", "rules", payload=request.get_json())


@app.get("/api/rules/<uuid>")
def get_rule(uuid: str):
    return api_response("GET", "rules/" + uuid)


@app.put("/api/rules/<uuid>")
@app.patch("/api/rules/<uuid>")
def update_rule(uuid: str):
    return api_response(request.method, "rules/" + uuid, payload=request.get_json())


@app.delete("/api/rules/<uuid>")
def delete_rule(uuid: str):
    params = {
        key: request.args.get(key)
        for key in ("if_version", "commit_message")
        if request.args.get(key) is not None
    }
    return api_response("DELETE", "rules/" + uuid, params=params)


@app.get("/api/rules/<uuid>/history")
def rule_history(uuid: str):
    params = {"limit": request.args.get("limit", "100")}
    return api_response("GET", "rules/" + uuid + "/history", params=params)


@app.get("/api/rules/<uuid>/revisions/<rev>")
def rule_revision(uuid: str, rev: str):
    return api_response("GET", "rules/" + uuid + "/revisions/" + rev)


@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "検索語を入力してください。"}), 400
    return api_response("GET", "search", params={"q": query, "limit": request.args.get("limit", "50")})


@app.get("/api/changes")
def changes():
    params = {
        key: request.args.get(key)
        for key in ("since", "limit")
        if request.args.get(key)
    }
    return api_response("GET", "changes", params=params)


@app.get("/api/git/status")
def git_status():
    return api_response("GET", "git/status")


@app.get("/api/git/log")
def git_log():
    return api_response("GET", "git/log", params={"limit": request.args.get("limit", "30")})


@app.post("/api/git/commit")
def git_commit():
    payload = request.get_json()
    if not isinstance(payload, dict) or not str(payload.get("message", "")).strip():
        return jsonify({"ok": False, "error": "コミットメッセージを入力してください。"}), 400
    return api_response("POST", "git/commit", payload={"message": str(payload["message"]).strip()})


@app.get("/api/validate")
def validate():
    return api_response("GET", "validate")


@app.errorhandler(403)
def forbidden(_error):
    return jsonify({"ok": False, "error": "この操作は許可されていません。"}), 403


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "LawWeb API endpoint not found"}), 404
    return render_template("index.html"), 404


if __name__ == "__main__":
    app.run(
        host=os.environ.get("WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("WEB_PORT", "5000")),
        debug=False,
    )
