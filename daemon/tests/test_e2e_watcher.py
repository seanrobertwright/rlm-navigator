"""E2E tests for file watcher integration: file changes trigger cache invalidation."""

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm_daemon import run_server
from tests.conftest import tcp_query, get_free_port


@pytest.mark.e2e
class TestFileWatcherE2E:
    def test_modify_invalidates_cache(self, tmp_path):
        """Modifying a file should invalidate its skeleton cache."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        f = tmp_path / "app.py"
        f.write_text("def original(): pass\n")

        port = get_free_port()
        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=0)
            shutdown_complete.set()

        t = threading.Thread(target=run_and_signal, daemon=True)
        t.start()

        # Wait for startup
        for _ in range(50):
            if (rlm_dir / "port").exists():
                break
            time.sleep(0.1)
        assert (rlm_dir / "port").exists(), "Daemon should start"

        try:
            # Initial squeeze
            resp = tcp_query(port, {"action": "squeeze", "path": "app.py"})
            assert "skeleton" in resp
            assert "original" in resp["skeleton"]

            # Modify file
            f.write_text("def modified(): pass\n")
            # Wait for watchdog event propagation
            time.sleep(2)

            # Re-squeeze — should reflect change
            resp = tcp_query(port, {"action": "squeeze", "path": "app.py"})
            assert "skeleton" in resp
            assert "modified" in resp["skeleton"]
        finally:
            tcp_query(port, {"action": "shutdown"})
            shutdown_complete.wait(timeout=10)

    def test_delete_removes_from_cache(self, tmp_path):
        """Deleting a file should make squeeze return an error."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        f = tmp_path / "temp.py"
        f.write_text("def temporary(): pass\n")

        port = get_free_port()
        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=0)
            shutdown_complete.set()

        t = threading.Thread(target=run_and_signal, daemon=True)
        t.start()

        for _ in range(50):
            if (rlm_dir / "port").exists():
                break
            time.sleep(0.1)
        assert (rlm_dir / "port").exists()

        try:
            # Initial squeeze works
            resp = tcp_query(port, {"action": "squeeze", "path": "temp.py"})
            assert "skeleton" in resp
            assert "temporary" in resp["skeleton"]

            # Delete file
            f.unlink()
            time.sleep(2)

            # Squeeze should fail
            resp = tcp_query(port, {"action": "squeeze", "path": "temp.py"})
            assert "error" in resp
        finally:
            tcp_query(port, {"action": "shutdown"})
            shutdown_complete.wait(timeout=10)
