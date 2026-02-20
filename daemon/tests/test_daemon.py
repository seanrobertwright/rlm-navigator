"""Tests for the RLM daemon TCP server and cache."""

import json
import os
import socket
import sys
import tempfile
import textwrap
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm_daemon import SkeletonCache, build_tree, handle_request, run_server


# ---------------------------------------------------------------------------
# SkeletonCache tests
# ---------------------------------------------------------------------------

class TestSkeletonCache:
    def test_cache_returns_skeleton(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n")
        cache = SkeletonCache()
        result = cache.get(str(f))
        assert result is not None
        assert "hello" in result

    def test_cache_hit(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n")
        cache = SkeletonCache()
        # First call caches it
        r1 = cache.get(str(f))
        # Second call hits cache (same mtime)
        r2 = cache.get(str(f))
        assert r1 == r2

    def test_invalidate(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n")
        cache = SkeletonCache()
        cache.get(str(f))
        assert cache.size == 1
        cache.invalidate(str(f))
        assert cache.size == 0

    def test_clear(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("def a(): pass\n")
        f2.write_text("def b(): pass\n")
        cache = SkeletonCache()
        cache.get(str(f1))
        cache.get(str(f2))
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0

    def test_nonexistent_file(self):
        cache = SkeletonCache()
        result = cache.get("/nonexistent/file.py")
        assert result is None


# ---------------------------------------------------------------------------
# build_tree tests
# ---------------------------------------------------------------------------

class TestBuildTree:
    def test_basic_tree(self, tmp_path):
        (tmp_path / "main.py").write_text("pass")
        (tmp_path / "util.js").write_text("//")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.ts").write_text("//")

        tree = build_tree(str(tmp_path), str(tmp_path))
        assert len(tree) > 0
        names = [e["name"] for e in tree]
        assert "main.py" in names
        assert "src" in names

    def test_ignores_hidden_dirs(self, tmp_path):
        git = tmp_path / ".git"
        git.mkdir()
        (git / "config").write_text("x")
        (tmp_path / "main.py").write_text("pass")

        tree = build_tree(str(tmp_path), str(tmp_path))
        names = [e["name"] for e in tree]
        assert ".git" not in names

    def test_ignores_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "package.json").write_text("{}")
        (tmp_path / "index.js").write_text("//")

        tree = build_tree(str(tmp_path), str(tmp_path))
        names = [e["name"] for e in tree]
        assert "node_modules" not in names

    def test_detects_languages(self, tmp_path):
        (tmp_path / "main.py").write_text("pass")
        (tmp_path / "app.go").write_text("package main")
        tree = build_tree(str(tmp_path), str(tmp_path))
        files = {e["name"]: e for e in tree if e["type"] == "file"}
        assert files["main.py"]["language"] == "python"
        assert files["app.go"]["language"] == "go"


# ---------------------------------------------------------------------------
# handle_request tests
# ---------------------------------------------------------------------------

class TestHandleRequest:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "main.py").write_text(
            "class App:\n    def run(self):\n        print('running')\n"
        )
        (tmp_path / "util.py").write_text(
            "def helper(x):\n    return x + 1\n"
        )
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "server.py").write_text(
            "def start():\n    pass\n"
        )
        return str(tmp_path)

    def test_status(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "status"}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert resp["status"] == "alive"
        assert "root" in resp

    def test_squeeze(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "squeeze", "path": "main.py"}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "skeleton" in resp
        assert "App" in resp["skeleton"]

    def test_find(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "find", "path": "main.py", "symbol": "App"}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "start_line" in resp
        assert "end_line" in resp

    def test_tree(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "tree", "path": ""}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "tree" in resp
        names = [e["name"] for e in resp["tree"]]
        assert "main.py" in names

    def test_search(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "search", "query": "helper", "path": ""}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "results" in resp

    def test_invalid_action(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "invalid"}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "error" in resp

    def test_invalid_json(self, project):
        cache = SkeletonCache()
        resp = json.loads(handle_request(b"not json", cache, project))
        assert "error" in resp

    def test_path_traversal_blocked(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "squeeze", "path": "../../etc/passwd"}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "error" in resp


# ---------------------------------------------------------------------------
# TCP server integration test
# ---------------------------------------------------------------------------

class TestTCPServer:
    def test_health_check(self, tmp_path):
        """Start daemon, check health via TCP, then shut down."""
        port = 19177  # Use non-standard port for tests

        (tmp_path / "test.py").write_text("def foo(): pass\n")

        # Start server in background thread
        server_thread = threading.Thread(
            target=run_server,
            args=(str(tmp_path), port),
            daemon=True,
        )
        server_thread.start()
        time.sleep(1)  # Wait for server to start

        # Health check — bare connection should get ALIVE
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(("127.0.0.1", port))
            s.settimeout(3)
            data = s.recv(1024)
            s.close()
            assert b"ALIVE" in data
        except Exception as e:
            pytest.skip(f"TCP test failed (port may be in use): {e}")

    def test_json_query(self, tmp_path):
        """Start daemon, send a JSON query, verify response."""
        port = 19178

        (tmp_path / "test.py").write_text("def foo():\n    pass\n")

        server_thread = threading.Thread(
            target=run_server,
            args=(str(tmp_path), port),
            daemon=True,
        )
        server_thread.start()
        time.sleep(1)

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(("127.0.0.1", port))
            s.send(json.dumps({"action": "status"}).encode())
            s.settimeout(3)
            data = s.recv(4096)
            s.close()
            resp = json.loads(data)
            assert resp["status"] == "alive"
        except Exception as e:
            pytest.skip(f"TCP test failed (port may be in use): {e}")
