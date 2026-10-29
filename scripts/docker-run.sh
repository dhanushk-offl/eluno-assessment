#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
port="${APP_PORT:-3000}"
if ! [[ "$port" =~ ^[0-9]+$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
  echo 'APP_PORT must be a valid port number (1–65535).' >&2
  exit 1
fi
docker build -t eluno-pos .
echo "Starting Eluno POS at http://localhost:$port (Ctrl-C to stop)."
exec docker run --rm -p "$port:3000" eluno-pos
