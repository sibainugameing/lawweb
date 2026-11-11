#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

if [ -f ".env" ]; then
  set -a
  # .env is a local shell-style configuration file. Keep it private and do not download it.
  . ./.env
  set +a
else
  cp .env.example .env
  echo "[lawweb] .env を作成しました。LAWGIT_REPOを設定してから再実行してください。"
  exit 1
fi

PYTHON=${PYTHON:-python3}
LAWGIT_BIN=${LAWGIT_BIN:-lawgit}
LAWGIT_API_HOST=${LAWGIT_API_HOST:-127.0.0.1}
LAWGIT_API_PORT=${LAWGIT_API_PORT:-8787}
LAWGIT_API_URL=${LAWGIT_API_URL:-http://${LAWGIT_API_HOST}:${LAWGIT_API_PORT}}
LAWGIT_TOKEN=${LAWGIT_TOKEN:-}
LAWGIT_COMMIT=${LAWGIT_COMMIT:-0}
WEB_HOST=${WEB_HOST:-127.0.0.1}
WEB_PORT=${WEB_PORT:-5000}
VENV_DIR="$ROOT/.venv"
LAW_PID=""
LAW_LOG="${TMPDIR:-/tmp}/lawweb-lawgit-$$.log"

api_is_ready() {
  if [ -n "$LAWGIT_TOKEN" ]; then
    curl -fsS --max-time 1 -H "Authorization: Bearer $LAWGIT_TOKEN" "$LAWGIT_API_URL/api/v1/ping" >/dev/null 2>&1
  else
    curl -fsS --max-time 1 "$LAWGIT_API_URL/api/v1/ping" >/dev/null 2>&1
  fi
}

cleanup() {
  if [ -n "$LAW_PID" ]; then
    kill "$LAW_PID" >/dev/null 2>&1 || true
    wait "$LAW_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if api_is_ready; then
  echo "[lawweb] 既存のLawGit APIを使用します: $LAWGIT_API_URL"
else
  if [ -z "${LAWGIT_REPO:-}" ]; then
    echo "[lawweb] エラー: .env の LAWGIT_REPO にLawGitデータリポジトリのパスを設定してください。" >&2
    echo "[lawweb] laws/ と rules/ を含むディレクトリを指定します。" >&2
    exit 1
  fi
  if [ ! -d "$LAWGIT_REPO" ]; then
    echo "[lawweb] エラー: LAWGIT_REPO が存在しません: $LAWGIT_REPO" >&2
    exit 1
  fi
  if ! command -v "$LAWGIT_BIN" >/dev/null 2>&1 && [ ! -x "$LAWGIT_BIN" ]; then
    echo "[lawweb] エラー: lawgit コマンドが見つかりません: $LAWGIT_BIN" >&2
    echo "[lawweb] cargo install --path <LawGitソースディレクトリ> などでCLIを用意してください。" >&2
    exit 1
  fi

  set --
  if [ -n "$LAWGIT_TOKEN" ]; then
    set -- "$@" --token "$LAWGIT_TOKEN"
  fi
  if [ "$LAWGIT_COMMIT" = "1" ]; then
    set -- "$@" --commit
  fi
  echo "[lawweb] LawGit APIを起動します…"
  "$LAWGIT_BIN" --repo "$LAWGIT_REPO" serve --host "$LAWGIT_API_HOST" --port "$LAWGIT_API_PORT" "$@" >"$LAW_LOG" 2>&1 &
  LAW_PID=$!

  ready=0
  attempt=0
  while [ "$attempt" -lt 40 ]; do
    if api_is_ready; then ready=1; break; fi
    if ! kill -0 "$LAW_PID" 2>/dev/null; then break; fi
    attempt=$((attempt + 1))
    sleep 0.25
  done
  if [ "$ready" -ne 1 ]; then
    echo "[lawweb] LawGit APIの起動に失敗しました。ログ:" >&2
    cat "$LAW_LOG" >&2 2>/dev/null || true
    exit 1
  fi
  echo "[lawweb] LawGit API起動完了"
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[lawweb] Python仮想環境を作成します…"
  "$PYTHON" -m venv "$VENV_DIR"
fi

echo "[lawweb] Python依存関係を確認します…"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r requirements.txt

echo "[lawweb] Web UI: http://${WEB_HOST}:${WEB_PORT}"
echo "[lawweb] 停止するには Ctrl+C を押してください。"
"$VENV_DIR/bin/python" -m flask --app app run --host "$WEB_HOST" --port "$WEB_PORT" --no-debugger --no-reload
