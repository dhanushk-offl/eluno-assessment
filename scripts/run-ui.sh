#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v bun >/dev/null || { echo 'Bun is required. Install Bun and retry.' >&2; exit 1; }
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --quiet -r requirements.txt
bun install --cwd ui --frozen-lockfile
mkdir -p .runtime
mock_pid=''
api_pid=''
cleanup() {
  if [ -n "$api_pid" ]; then kill "$api_pid" 2>/dev/null || true; fi
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
mock_url="${MOCK_URL:-http://127.0.0.1:8787}"
api_port="${PORT:-8000}"
ui_port="${UI_PORT:-5173}"
if ! .venv/bin/python - "$api_port" <<'PYCODE'
import socket, sys
with socket.socket() as sock:
    try: sock.bind(('127.0.0.1', int(sys.argv[1])))
    except OSError: raise SystemExit(1)
PYCODE
then
  echo "API port $api_port is occupied. Stop that service or choose PORT." >&2
  exit 1
fi
if [ -z "${MOCK_URL:-}" ]; then
  if .venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=1)' >/dev/null 2>&1; then
    echo 'Port 8787 is occupied. Stop the existing mock or set MOCK_URL.' >&2
    exit 1
  fi
  .venv/bin/python starter-kit/mock-apis/mock_server.py >.runtime/mock.log 2>&1 &
  mock_pid=$!
fi
wait_for() {
  local endpoint="$1" pid="$2" logfile="$3" ready=0
  for attempt in {1..80}; do
    if .venv/bin/python -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1], timeout=1)' "$endpoint" >/dev/null 2>&1; then ready=1; break; fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then cat "$logfile" >&2; return 1; fi
    sleep 0.1
  done
  if [ "$ready" = 0 ]; then echo "Service did not start: $endpoint" >&2; return 1; fi
}
wait_for "$mock_url/health" "$mock_pid" .runtime/mock.log
MOCK_URL="$mock_url" .venv/bin/python -m uvicorn pos.api:create_app --factory --host 127.0.0.1 --port "$api_port" >.runtime/api.log 2>&1 &
api_pid=$!
wait_for "http://127.0.0.1:$api_port/health" "$api_pid" .runtime/api.log
ui_host=127.0.0.1
if [ "${TABLET_ACCESS:-0}" = 1 ]; then ui_host=0.0.0.0; fi
printf '\nEluno POS: http://localhost:%s\nAPI docs: http://localhost:%s/docs\n\n' "$ui_port" "$api_port"
POS_API_URL="http://127.0.0.1:$api_port" bun run --cwd ui dev --host "$ui_host" --port "$ui_port" --strictPort
