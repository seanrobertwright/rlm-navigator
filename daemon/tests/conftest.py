"""Shared fixtures for E2E and integration tests."""

import json
import os
import random
import socket
import sys
import threading
import time

import pytest

# Add daemon directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests (may be slow)")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "requires_api: tests requiring real API keys")


# ---------------------------------------------------------------------------
# TCP helpers
# ---------------------------------------------------------------------------

def tcp_query(port: int, request: dict, timeout: float = 5.0) -> dict:
    """Send a JSON request to the daemon and return parsed response."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", port))
    s.sendall(json.dumps(request).encode())
    data = b""
    while True:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        except socket.timeout:
            break
    s.close()
    return json.loads(data.decode("utf-8"))


def get_free_port() -> int:
    """Get a random free port in the test range."""
    base = 19200 + random.randint(0, 500)
    for port in range(base, base + 20):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError("No free port found in test range")


# ---------------------------------------------------------------------------
# Daemon server fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def daemon_server(tmp_path):
    """Start a daemon server and yield (port, root). Cleanup on teardown."""
    from rlm_daemon import run_server

    port = get_free_port()
    rlm_dir = tmp_path / ".rlm"
    rlm_dir.mkdir()

    started = threading.Event()
    shutdown_complete = threading.Event()

    def run():
        run_server(str(tmp_path), port, idle_timeout=0)
        shutdown_complete.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Wait for port file to appear (confirms server is ready)
    for _ in range(50):
        if (rlm_dir / "port").exists():
            started.set()
            break
        time.sleep(0.1)

    if not started.is_set():
        pytest.skip("Daemon failed to start in time")

    yield port, str(tmp_path)

    # Teardown: send shutdown
    try:
        tcp_query(port, {"action": "shutdown"}, timeout=2)
    except Exception:
        pass
    shutdown_complete.wait(timeout=5)
