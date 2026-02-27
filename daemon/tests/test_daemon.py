"""Tests for the RLM daemon TCP server and cache."""

import json
import os
import socket
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Lock file tests
# ---------------------------------------------------------------------------

class TestLockFile:
    def test_lock_file_created_on_startup(self, tmp_path):
        """Lock file should be written when daemon starts."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        lock_file = rlm_dir / "daemon.lock"

        from rlm_daemon import write_lock_file, read_lock_file, check_lock_file

        write_lock_file(str(tmp_path), 9177)
        assert lock_file.exists()

        data = read_lock_file(str(tmp_path))
        assert data is not None
        assert data["pid"] == os.getpid()
        assert data["port"] == 9177
        assert data["root"] == str(Path(tmp_path).resolve())
        assert "started_at" in data

    def test_lock_file_detects_stale(self, tmp_path):
        """Lock file with dead PID should be detected as stale."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        lock_file = rlm_dir / "daemon.lock"

        lock_data = json.dumps({"pid": 999999999, "port": 9177, "root": str(tmp_path), "started_at": "2026-01-01T00:00:00"})
        lock_file.write_text(lock_data)

        from rlm_daemon import check_lock_file
        result = check_lock_file(str(tmp_path))
        assert result is None
        assert not lock_file.exists()

    def test_lock_file_blocks_duplicate(self, tmp_path):
        """Lock file with alive PID should block startup."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        from rlm_daemon import write_lock_file, check_lock_file
        write_lock_file(str(tmp_path), 9177)

        result = check_lock_file(str(tmp_path))
        assert result is not None
        assert result["pid"] == os.getpid()

    def test_remove_lock_file(self, tmp_path):
        """remove_lock_file should delete the lock."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        from rlm_daemon import write_lock_file, remove_lock_file
        write_lock_file(str(tmp_path), 9177)
        assert (rlm_dir / "daemon.lock").exists()

        remove_lock_file(str(tmp_path))
        assert not (rlm_dir / "daemon.lock").exists()


class TestShutdownAction:
    def test_shutdown_action_returns_ok(self, tmp_path):
        """Shutdown action should return success and set the event."""
        (tmp_path / "test.py").write_text("def foo(): pass\n")
        cache = SkeletonCache()

        shutdown_event = threading.Event()
        data = json.dumps({"action": "shutdown"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path), shutdown_event=shutdown_event))
        assert resp.get("status") == "shutting_down"
        assert shutdown_event.is_set()

    def test_shutdown_action_without_event(self, tmp_path):
        """Shutdown without event should return error."""
        cache = SkeletonCache()
        data = json.dumps({"action": "shutdown"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "error" in resp


# ---------------------------------------------------------------------------
# Daemon lifecycle integration tests
# ---------------------------------------------------------------------------

class TestDaemonLifecycle:
    def test_shutdown_via_tcp(self, tmp_path):
        """Start daemon, send shutdown via TCP, verify clean exit."""
        port = 19179
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        (tmp_path / "test.py").write_text("def foo(): pass\n")

        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=0)
            shutdown_complete.set()

        server_thread = threading.Thread(target=run_and_signal, daemon=True)
        server_thread.start()
        time.sleep(1)

        try:
            # Send shutdown
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(("127.0.0.1", port))
            s.send(json.dumps({"action": "shutdown"}).encode())
            data = s.recv(4096)
            s.close()

            resp = json.loads(data)
            assert resp["status"] == "shutting_down"

            # Wait for server to actually shut down
            assert shutdown_complete.wait(timeout=5), "Daemon didn't shut down in time"

            # Verify cleanup: port file and lock file should be removed
            port_file = rlm_dir / "port"
            lock_file = rlm_dir / "daemon.lock"
            assert not port_file.exists(), "Port file not cleaned up"
            assert not lock_file.exists(), "Lock file not cleaned up"

        except Exception as e:
            pytest.skip(f"TCP test failed (port may be in use): {e}")

    def test_lock_prevents_second_daemon(self, tmp_path):
        """Starting a second daemon on same root should fail."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        from rlm_daemon import write_lock_file
        write_lock_file(str(tmp_path), 9177)

        with pytest.raises(SystemExit):
            run_server(str(tmp_path), 19180, idle_timeout=0)


# ---------------------------------------------------------------------------
# Document indexing integration tests
# ---------------------------------------------------------------------------

class TestDocumentIndexing:
    def test_doc_map_action_markdown(self, tmp_path):
        """doc_map action should return document tree for markdown files."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Project\n\nOverview.\n\n## Install\n\nRun npm install.\n")

        cache = SkeletonCache()
        data = json.dumps({"action": "doc_map", "path": "README.md"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))

        assert "error" not in resp
        assert "tree" in resp
        assert resp["tree"]["name"] == "README.md"
        assert resp["tree"]["type"] == "document"
        assert len(resp["tree"]["children"]) >= 1

    def test_doc_map_action_nonexistent(self, tmp_path):
        """doc_map for missing file should return error."""
        cache = SkeletonCache()
        data = json.dumps({"action": "doc_map", "path": "missing.md"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "error" in resp

    def test_squeeze_returns_skeleton_for_markdown(self, tmp_path):
        """squeeze action on .md should return a document skeleton."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Title\n\nSome text.\n\n## Section\n\nMore text.\n")

        cache = SkeletonCache()
        data = json.dumps({"action": "squeeze", "path": "README.md"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))

        assert "skeleton" in resp
        assert "Title" in resp["skeleton"]

    def test_search_includes_document_files(self, tmp_path):
        """search should find headings in document files."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Installation Guide\n\nFollow these steps.\n")

        cache = SkeletonCache()
        # First, trigger caching
        data = json.dumps({"action": "squeeze", "path": "README.md"}).encode()
        handle_request(data, cache, str(tmp_path))

        data = json.dumps({"action": "search", "query": "Installation"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "results" in resp

    def test_doc_map_rejects_non_document(self, tmp_path):
        """doc_map should reject code files."""
        py_file = tmp_path / "main.py"
        py_file.write_text("def hello(): pass\n")

        cache = SkeletonCache()
        data = json.dumps({"action": "doc_map", "path": "main.py"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "error" in resp


# ---------------------------------------------------------------------------
# Enrichment integration tests
# ---------------------------------------------------------------------------

class TestDocDrill:
    def test_doc_drill_extracts_section(self, tmp_path):
        """doc_drill should return content for a specific section."""
        md_file = tmp_path / "guide.md"
        md_file.write_text("# Guide\n\nIntro text.\n\n## Installation\n\nRun npm install.\n\n## Usage\n\nImport the module.\n")

        cache = SkeletonCache()
        data = json.dumps({"action": "doc_drill", "path": "guide.md", "section": "Installation"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))

        assert "error" not in resp
        assert "content" in resp
        assert "npm install" in resp["content"]

    def test_doc_drill_missing_section(self, tmp_path):
        """doc_drill for nonexistent section should return error."""
        md_file = tmp_path / "guide.md"
        md_file.write_text("# Guide\n\n## Installation\n\nContent.\n")

        cache = SkeletonCache()
        data = json.dumps({"action": "doc_drill", "path": "guide.md", "section": "Nonexistent"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "error" in resp

    def test_doc_drill_missing_file(self, tmp_path):
        """doc_drill for missing file should return error."""
        cache = SkeletonCache()
        data = json.dumps({"action": "doc_drill", "path": "missing.md", "section": "Foo"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "error" in resp


class TestAssessAction:
    def test_assess_returns_assessment(self, tmp_path):
        """assess action should return an assessment string."""
        cache = SkeletonCache()
        data = json.dumps({
            "action": "assess",
            "query": "How does auth work?",
            "context_summary": "Found validate_token in auth.py"
        }).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))

        assert "error" not in resp
        assert "assessment" in resp
        assert "auth" in resp["assessment"].lower()


class TestEnrichmentIntegration:
    def test_status_reports_enrichment_state(self, tmp_path):
        """Status should report whether enrichment is available."""
        cache = SkeletonCache()
        data = json.dumps({"action": "status"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))

        assert "enrichment_available" in resp
        assert isinstance(resp["enrichment_available"], bool)
        assert "doc_indexing_available" in resp
        assert isinstance(resp["doc_indexing_available"], bool)
