"""Tests for the RLM REPL — stateful execution environment."""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm_repl import RLMRepl
from rlm_daemon import SkeletonCache, handle_request


# ---------------------------------------------------------------------------
# TestReplInit
# ---------------------------------------------------------------------------

class TestReplInit:
    def test_creates_state_dir(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        assert os.path.isdir(repl.state_dir)

    def test_fresh_state(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        result = repl.init()
        assert result["success"] is True
        status = repl.status()
        assert status["variables"] == []
        assert status["exec_count"] == 0


# ---------------------------------------------------------------------------
# TestReplExec
# ---------------------------------------------------------------------------

class TestReplExec:
    def test_simple_expression(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("x = 42\nprint(x)")
        assert result["success"] is True
        assert "42" in result["output"]
        assert "x" in result["variables"]

    def test_state_persistence_between_calls(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("counter = 0")
        repl.exec("counter += 10")
        result = repl.exec("print(counter)")
        assert "10" in result["output"]

    def test_pickle_persistence_across_instances(self, tmp_path):
        state_dir = str(tmp_path / "state")
        repl1 = RLMRepl(str(tmp_path), state_dir=state_dir)
        repl1.init()
        repl1.exec("saved_value = 'hello_from_repl1'")

        # New instance, same state dir
        repl2 = RLMRepl(str(tmp_path), state_dir=state_dir)
        result = repl2.exec("print(saved_value)")
        assert "hello_from_repl1" in result["output"]

    def test_error_handling(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("1/0")
        assert result["success"] is False
        assert "error" in result
        assert "ZeroDivisionError" in result["error"]

    def test_output_truncation(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        # Generate output exceeding 8000 chars
        result = repl.exec("print('x' * 20000)")
        assert "truncated" in result["output"]
        assert len(result["output"]) < 20000

    def test_import_persistence(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("import math")
        result = repl.exec("print(math.pi)")
        assert result["success"] is True
        assert "3.14" in result["output"]

    def test_exec_count_increments(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("x = 1")
        repl.exec("y = 2")
        status = repl.status()
        assert status["exec_count"] == 2


# ---------------------------------------------------------------------------
# TestReplHelpers
# ---------------------------------------------------------------------------

class TestReplHelpers:
    def test_peek(self, tmp_path):
        (tmp_path / "sample.py").write_text("line1\nline2\nline3\nline4\nline5\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("print(peek('sample.py', 2, 4))")
        assert result["success"] is True
        assert "line2" in result["output"]
        assert "line4" in result["output"]
        # Should NOT include line1 or line5
        assert "line1" not in result["output"]

    def test_peek_nonexistent(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("print(peek('nope.py'))")
        assert "not found" in result["output"]

    def test_grep(self, tmp_path):
        (tmp_path / "a.py").write_text("def hello():\n    pass\n")
        (tmp_path / "b.py").write_text("def world():\n    pass\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("print(grep('hello'))")
        assert result["success"] is True
        assert "a.py" in result["output"]
        assert "hello" in result["output"]

    def test_grep_no_matches(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo(): pass\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("print(grep('zzz_nonexistent'))")
        assert "No matches" in result["output"]

    def test_chunk_indices(self, tmp_path):
        # Create a 500-line file
        lines = [f"line {i}\n" for i in range(500)]
        (tmp_path / "big.py").write_text("".join(lines))
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("chunks = chunk_indices('big.py', size=200, overlap=20)\nprint(len(chunks))\nprint(chunks[0])")
        assert result["success"] is True
        assert "(1, 200)" in result["output"]

    def test_chunk_indices_nonexistent(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("print(chunk_indices('nope.py'))")
        assert "[]" in result["output"]

    def test_write_chunks(self, tmp_path):
        lines = [f"line {i}\n" for i in range(100)]
        (tmp_path / "medium.py").write_text("".join(lines))
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.exec("paths = write_chunks('medium.py', size=50, overlap=10)\nprint(len(paths))")
        assert result["success"] is True
        # Verify chunk files exist
        result2 = repl.exec("import os; print(os.path.exists(paths[0]))")
        assert "True" in result2["output"]


# ---------------------------------------------------------------------------
# TestReplBuffers
# ---------------------------------------------------------------------------

class TestReplBuffers:
    def test_add_and_export(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("add_buffer('findings', 'Found auth in middleware.py')")
        repl.exec("add_buffer('findings', 'Token validation in auth.py')")
        repl.exec("add_buffer('todos', 'Review error handling')")
        result = repl.export_buffers()
        assert "findings" in result["buffers"]
        assert len(result["buffers"]["findings"]) == 2
        assert len(result["buffers"]["todos"]) == 1

    def test_buffer_persistence(self, tmp_path):
        state_dir = str(tmp_path / "state")
        repl1 = RLMRepl(str(tmp_path), state_dir=state_dir)
        repl1.init()
        repl1.exec("add_buffer('key', 'value1')")

        repl2 = RLMRepl(str(tmp_path), state_dir=state_dir)
        result = repl2.export_buffers()
        assert "key" in result["buffers"]
        assert result["buffers"]["key"] == ["value1"]

    def test_export_empty(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        result = repl.export_buffers()
        assert result["buffers"] == {}


# ---------------------------------------------------------------------------
# TestReplReset
# ---------------------------------------------------------------------------

class TestReplReset:
    def test_clears_variables(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("x = 42")
        repl.exec("add_buffer('key', 'val')")
        repl.reset()
        status = repl.status()
        assert status["variables"] == []
        assert status["buffer_count"] == {}

    def test_clears_pickle(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("x = 42")
        assert os.path.exists(repl.state_path)
        repl.reset()
        assert not os.path.exists(repl.state_path)


# ---------------------------------------------------------------------------
# TestReplDaemonIntegration
# ---------------------------------------------------------------------------

class TestReplDaemonIntegration:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "main.py").write_text("def hello(): pass\n")
        return str(tmp_path)

    def test_repl_init_via_daemon(self, project):
        cache = SkeletonCache()
        repl = RLMRepl(project)
        data = json.dumps({"action": "repl_init"}).encode()
        resp = json.loads(handle_request(data, cache, project, repl))
        assert resp["success"] is True

    def test_repl_exec_via_daemon(self, project):
        cache = SkeletonCache()
        repl = RLMRepl(project)
        # Init first
        handle_request(json.dumps({"action": "repl_init"}).encode(), cache, project, repl)
        # Execute code
        data = json.dumps({"action": "repl_exec", "code": "x = 42\nprint(x)"}).encode()
        resp = json.loads(handle_request(data, cache, project, repl))
        assert resp["success"] is True
        assert "42" in resp["output"]

    def test_repl_status_via_daemon(self, project):
        cache = SkeletonCache()
        repl = RLMRepl(project)
        handle_request(json.dumps({"action": "repl_init"}).encode(), cache, project, repl)
        data = json.dumps({"action": "repl_status"}).encode()
        resp = json.loads(handle_request(data, cache, project, repl))
        assert "variables" in resp

    def test_repl_export_via_daemon(self, project):
        cache = SkeletonCache()
        repl = RLMRepl(project)
        handle_request(json.dumps({"action": "repl_init"}).encode(), cache, project, repl)
        handle_request(
            json.dumps({"action": "repl_exec", "code": "add_buffer('k', 'v')"}).encode(),
            cache, project, repl
        )
        data = json.dumps({"action": "repl_export_buffers"}).encode()
        resp = json.loads(handle_request(data, cache, project, repl))
        assert "buffers" in resp
        assert resp["buffers"]["k"] == ["v"]

    def test_repl_none_returns_error(self, project):
        cache = SkeletonCache()
        data = json.dumps({"action": "repl_init"}).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "error" in resp


# ---------------------------------------------------------------------------
# TestStalenessTracking
# ---------------------------------------------------------------------------

class TestStalenessTracking:
    def test_peek_tracks_file(self, tmp_path):
        (tmp_path / "auth.py").write_text("def login(): pass\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("result = peek('auth.py')")
        deps = repl._namespace["_rlm_deps_"]
        assert "result" in deps["variables"]
        assert "auth.py" in deps["variables"]["result"]["files"]

    def test_grep_tracks_files(self, tmp_path):
        (tmp_path / "a.py").write_text("def hello(): pass\n")
        (tmp_path / "b.py").write_text("def world(): pass\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("results = grep('def')")
        deps = repl._namespace["_rlm_deps_"]
        assert "results" in deps["variables"]
        tracked_files = deps["variables"]["results"]["files"]
        assert "a.py" in tracked_files
        assert "b.py" in tracked_files

    def test_staleness_on_modify(self, tmp_path):
        (tmp_path / "auth.py").write_text("def login(): pass\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("data = peek('auth.py')")
        # No staleness yet
        assert repl._check_staleness() is None
        # Modify the file
        time.sleep(0.05)
        (tmp_path / "auth.py").write_text("def login(): return True\n")
        staleness = repl._check_staleness()
        assert staleness is not None
        assert "data" in staleness["variables"]
        assert staleness["variables"]["data"][0]["reason"] == "modified"

    def test_staleness_on_delete(self, tmp_path):
        (tmp_path / "temp.py").write_text("x = 1\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("data = peek('temp.py')")
        os.remove(str(tmp_path / "temp.py"))
        staleness = repl._check_staleness()
        assert staleness is not None
        assert "data" in staleness["variables"]
        assert staleness["variables"]["data"][0]["reason"] == "deleted"

    def test_buffer_staleness(self, tmp_path):
        (tmp_path / "api.py").write_text("def endpoint(): pass\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("data = peek('api.py')\nadd_buffer('findings', data)")
        assert repl._check_staleness() is None
        time.sleep(0.05)
        (tmp_path / "api.py").write_text("def endpoint(): return 42\n")
        staleness = repl._check_staleness()
        assert staleness is not None
        assert "findings" in staleness["buffers"]

    def test_additive_merge(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("data = peek('a.py')")
        repl.exec("data = peek('b.py')")
        deps = repl._namespace["_rlm_deps_"]
        files = deps["variables"]["data"]["files"]
        # Both files should be tracked (additive)
        assert "a.py" in files
        assert "b.py" in files

    def test_status_includes_staleness(self, tmp_path):
        (tmp_path / "mod.py").write_text("val = 1\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("content = peek('mod.py')")
        status = repl.status()
        assert "staleness" not in status
        time.sleep(0.05)
        (tmp_path / "mod.py").write_text("val = 2\n")
        status = repl.status()
        assert "staleness" in status
        assert "content" in status["staleness"]["variables"]

    def test_no_staleness_without_file_ops(self, tmp_path):
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("x = 42")
        deps = repl._namespace["_rlm_deps_"]
        # x should not have deps since no file helpers were used
        assert "x" not in deps["variables"] or not deps["variables"].get("x", {}).get("files")
        assert repl._check_staleness() is None

    def test_invalidate_dependencies(self, tmp_path):
        (tmp_path / "target.py").write_text("a = 1\n")
        repl = RLMRepl(str(tmp_path))
        repl.init()
        repl.exec("data = peek('target.py')")
        repl.exec("peek('target.py')\nadd_buffer('notes', 'found something')")
        affected = repl.invalidate_dependencies(str(tmp_path / "target.py"))
        assert "var:data" in affected
        assert "buffer:notes" in affected
