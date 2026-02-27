"""E2E tests for daemon lifecycle: startup, status, shutdown, cleanup, idle timeout, lock files."""

import json
import os
import sys
import socket
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm_daemon import run_server, check_lock_file, write_lock_file, remove_lock_file
from tests.conftest import tcp_query, get_free_port


@pytest.mark.e2e
class TestDaemonLifecycleE2E:
    def test_full_lifecycle(self, tmp_path):
        """Start → status → shutdown → cleanup."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (tmp_path / "main.py").write_text("def hello(): pass\n")

        port = get_free_port()
        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=0)
            shutdown_complete.set()

        t = threading.Thread(target=run_and_signal, daemon=True)
        t.start()

        # Wait for port file to appear
        for _ in range(50):
            if (rlm_dir / "port").exists():
                break
            time.sleep(0.1)

        # Verify port + lock files written
        assert (rlm_dir / "port").exists(), "Port file should exist after startup"
        assert (rlm_dir / "daemon.lock").exists(), "Lock file should exist after startup"

        port_data = json.loads((rlm_dir / "port").read_text())
        assert port_data["port"] == port
        assert "pid" in port_data

        # Status check via TCP
        resp = tcp_query(port, {"action": "status"})
        assert resp["status"] == "alive"
        assert resp["cache_size"] >= 0

        # Shutdown via TCP
        resp = tcp_query(port, {"action": "shutdown"})
        assert resp["status"] == "shutting_down"
        assert shutdown_complete.wait(timeout=10), "Daemon should shut down after shutdown command"

        # Verify cleanup
        assert not (rlm_dir / "port").exists(), "Port file should be removed after shutdown"
        assert not (rlm_dir / "daemon.lock").exists(), "Lock file should be removed after shutdown"

    def test_idle_timeout_shutdown(self, tmp_path):
        """Daemon should auto-shutdown after idle timeout."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (tmp_path / "test.py").write_text("x = 1\n")

        port = get_free_port()
        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=2)
            shutdown_complete.set()

        t = threading.Thread(target=run_and_signal, daemon=True)
        t.start()

        # Wait for startup
        for _ in range(50):
            if (rlm_dir / "port").exists():
                break
            time.sleep(0.1)
        assert (rlm_dir / "port").exists(), "Daemon should start"

        # Don't send any requests — wait for idle timeout
        assert shutdown_complete.wait(timeout=15), "Daemon should auto-shutdown after idle timeout"

        # Cleanup should have happened
        assert not (rlm_dir / "port").exists(), "Port file should be removed after idle shutdown"

    def test_stale_lock_recovery(self, tmp_path):
        """Stale lock file (dead PID) should be cleaned up."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        # Write a lock file with a dead PID
        lock_path = rlm_dir / "daemon.lock"
        lock_data = {"pid": 999999999, "port": 19999, "root": str(tmp_path)}
        lock_path.write_text(json.dumps(lock_data))

        # Also write a stale port file
        port_path = rlm_dir / "port"
        port_path.write_text(json.dumps({"port": 19999, "pid": 999999999}))

        # check_lock_file should detect dead PID and clean up
        result = check_lock_file(str(tmp_path))
        assert result is None, "Stale lock should return None"
        assert not lock_path.exists(), "Stale lock file should be deleted"
        assert not port_path.exists(), "Stale port file should be deleted"

    def test_duplicate_daemon_blocked(self, tmp_path):
        """Second daemon on same root should be blocked by lock file."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (tmp_path / "test.py").write_text("x = 1\n")

        port1 = get_free_port()
        shutdown_complete = threading.Event()

        def run_first():
            run_server(str(tmp_path), port1, idle_timeout=0)
            shutdown_complete.set()

        t1 = threading.Thread(target=run_first, daemon=True)
        t1.start()

        # Wait for first daemon to start
        for _ in range(50):
            if (rlm_dir / "port").exists():
                break
            time.sleep(0.1)
        assert (rlm_dir / "port").exists(), "First daemon should start"

        # Second daemon should exit with SystemExit
        port2 = get_free_port()
        with pytest.raises(SystemExit):
            run_server(str(tmp_path), port2, idle_timeout=0)

        # Clean up first daemon
        tcp_query(port1, {"action": "shutdown"})
        shutdown_complete.wait(timeout=10)
