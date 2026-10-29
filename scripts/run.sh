#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet -r requirements.txt
mkdir -p .runtime
mock_pid=''
cleanup() {
  if [ -n "$mock_pid" ]; then kill "$mock_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
if [ -z "${MOCK_URL:-}" ]; then
  if .venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=1)' >/dev/null 2>&1; then
    echo 'Port 8787 is occupied. Stop the existing mock, or set MOCK_URL explicitly.' >&2
    exit 1
  fi
  .venv/bin/python starter-kit/mock-apis/mock_server.py >.runtime/mock.log 2>&1 &
  mock_pid=$!
  ready=0
  for attempt in {1..40}; do
    if .venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=1)' >/dev/null 2>&1; then ready=1; break; fi
    if ! kill -0 "$mock_pid" 2>/dev/null; then cat .runtime/mock.log; exit 1; fi
    sleep 0.1
  done
  if [ "$ready" = 0 ]; then cat .runtime/mock.log; exit 1; fi
fi
.venv/bin/python -m uvicorn pos.api:create_app --factory --host 127.0.0.1 --port "${PORT:-8000}"
