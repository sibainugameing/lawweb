#!/bin/sh
set -eu
umask 077

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

if [ ! -f ".env" ]; then
  cp .env.example .env
  chmod 600 .env
  echo "[lawweb] .env を作成しました。LAWGIT_REPOを設定してから再実行してください。"
  exit 1
fi

chmod 600 .env
set -a
. ./.env
set +a

PYTHON=${PYTHON:-python3}
LAWGIT_BIN=${LAWGIT_BIN:-lawgit}
LAWGIT_API_HOST=${LAWGIT_API_HOST:-127.0.0.1}
LAWGIT_API_PORT=${LAWGIT_API_PORT:-8787}
LAWGIT_API_URL="http://127.0.0.1:$LAWGIT_API_PORT"
LAWGIT_TOKEN=${LAWGIT_TOKEN:-}
LAWGIT_TIMEOUT=${LAWGIT_TIMEOUT:-10}
WEB_HOST=${WEB_HOST:-0.0.0.0}
WEB_PORT=${WEB_PORT:-5000}
VENV_DIR="$ROOT/.venv"
LAW_PID=""
LAW_LOG="${TMPDIR:-/tmp}/lawweb-lawgit-$$.log"

case "$WEB_HOST" in
  0.0.0.0|127.0.0.1) ;;
  *)
    echo "[lawweb] WEB_HOSTは0.0.0.0（LAN対応）または127.0.0.1（このPCのみ）を指定してください。" >&2
    exit 1
    ;;
esac
if [ "$LAWGIT_API_HOST" != "127.0.0.1" ]; then
  echo "[lawweb] 安全のためLAWGIT_API_HOSTは127.0.0.1固定です。" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "[lawweb] curlが必要です。" >&2
  exit 1
fi

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
  if [ -z "${LAWGIT_REPO:-}" ] || [ ! -d "$LAWGIT_REPO" ]; then
    echo "[lawweb] .envのLAWGIT_REPOに、laws/とrules/を含むデータリポジトリのパスを設定してください。" >&2
    exit 1
  fi
  if ! command -v "$LAWGIT_BIN" >/dev/null 2>&1 && [ ! -x "$LAWGIT_BIN" ]; then
    echo "[lawweb] lawgitコマンドが見つかりません: $LAWGIT_BIN" >&2
    exit 1
  fi

  set --
  if [ -n "$LAWGIT_TOKEN" ]; then set -- "$@" --token "$LAWGIT_TOKEN"; fi
  echo "[lawweb] LawGit APIを起動します…"
  "$LAWGIT_BIN" --repo "$LAWGIT_REPO" serve --host 127.0.0.1 --port "$LAWGIT_API_PORT" "$@" >"$LAW_LOG" 2>&1 &
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
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c 'import flask, requests, argon2, waitress' >/dev/null 2>&1; then
  echo "[lawweb] Python依存関係をインストールします…"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
fi

export LAWGIT_API_URL LAWGIT_TOKEN LAWGIT_TIMEOUT WEB_HOST WEB_PORT
echo "[lawweb] アカウント登録はWebの /register から行えます。"

echo "[lawweb] LawWeb: http://127.0.0.1:$WEB_PORT"
if [ "$WEB_HOST" = "0.0.0.0" ]; then
  echo "[lawweb] 同じLAN内の端末からは http://<このPCのLAN IP>:$WEB_PORT を開いてください。"
  echo "[lawweb] 公開IP・インターネットからのアクセスはアプリ側で拒否されます。"
else
  echo "[lawweb] LAN接続は無効です。LAN接続を使う場合は .env の WEB_HOST=0.0.0.0 に変更してください。"
fi
echo "[lawweb] 停止するには Ctrl+C を押してください。"
"$VENV_DIR/bin/waitress-serve" --host="$WEB_HOST" --port="$WEB_PORT" --threads=4 --channel-timeout=30 app:app
