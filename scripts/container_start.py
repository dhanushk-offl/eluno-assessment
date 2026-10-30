#!/usr/bin/env python3
"""Run the demo services together and stop the container if one exits."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
children = []
stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True


def start(name, command, env):
    print(f"Starting {name}", flush=True)
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    children.append((name, process))
    return process


def wait_ready(url, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not stopping:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before becoming ready: {url}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise RuntimeError(f"Service did not become ready: {url}")


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    env = os.environ.copy()
    public_port = int(env.get("PORT", "3000"))
    # Railway may assign a port that matches one of our usual internal ports.
    # Keep explicit overrides strict, but move defaults out of the way.
    api_port = int(env.get("API_INTERNAL_PORT", "8001" if public_port == 8000 else "8000"))
    mock_port = int(env.get("MOCK_INTERNAL_PORT", "8788" if 8787 in (public_port, api_port) else "8787"))
    if any(not 1 <= port <= 65535 for port in (public_port, api_port, mock_port)) or len({public_port, api_port, mock_port}) != 3:
        raise ValueError("PORT, API_INTERNAL_PORT and MOCK_INTERNAL_PORT must be distinct valid ports")
    env.update(PORT=str(public_port), HOST="0.0.0.0", POS_API_URL=f"http://127.0.0.1:{api_port}")
    # The built-in mock loses its state on restart, so its default ledger must
    # reset with it. An explicitly configured POS_DB is the operator's choice.
    with tempfile.TemporaryDirectory(prefix="eluno-pos-") as runtime:
        env.setdefault("POS_DB", str(Path(runtime) / "pos.sqlite3"))
        try:
            if not env.get("MOCK_URL"):
                env["MOCK_URL"] = f"http://127.0.0.1:{mock_port}"
                mock = start("mock", [sys.executable, "starter-kit/mock-apis/mock_server.py", "--host", "127.0.0.1", "--port", str(mock_port)], env)
                wait_ready(f"{env['MOCK_URL']}/health", mock)
            api = start("API", [sys.executable, "-m", "uvicorn", "pos.api:create_app", "--factory", "--host", "127.0.0.1", "--port", str(api_port), "--root-path", "/api"], env)
            wait_ready(f"http://127.0.0.1:{api_port}/health", api)
            start("frontend", ["bun", "ui/build/index.js"], env)
            while not stopping:
                for name, process in children:
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} exited unexpectedly ({process.returncode})")
                time.sleep(0.2)
        finally:
            for _, process in reversed(children):
                if process.poll() is None:
                    process.terminate()
            deadline = time.monotonic() + 5
            for _, process in reversed(children):
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Startup failed: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
