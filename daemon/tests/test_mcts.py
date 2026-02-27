import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMCTSSession:
    def test_create_session(self):
        from mcts import MCTSSession
        session = MCTSSession("How does auth work?")
        assert session.query == "How does auth work?"
        assert session.depth == 0
        assert len(session.visited) == 0
        assert len(session.blacklist) == 0

    def test_visit_node(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.visit("src/auth.py::validate_token")
        assert "src/auth.py::validate_token" in session.visited
        assert session.depth == 1

    def test_blacklist_node(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.blacklist_node("src/utils.py")
        assert "src/utils.py" in session.blacklist

    def test_score_node(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.set_score("src/auth.py", 0.9)
        assert session.get_score("src/auth.py") == 0.9

    def test_get_score_default(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        assert session.get_score("nonexistent") == 0.0

    def test_max_depth_enforcement(self):
        from mcts import MCTSSession
        session = MCTSSession("query", max_depth=3)
        for i in range(3):
            session.visit(f"node_{i}")
        assert session.at_max_depth

    def test_not_at_max_depth(self):
        from mcts import MCTSSession
        session = MCTSSession("query", max_depth=5)
        session.visit("node_0")
        assert not session.at_max_depth

    def test_add_context(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.add_context("snippet 1")
        session.add_context("snippet 2")
        assert len(session.context_accumulated) == 2

    def test_visit_deduplicates(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.visit("a.py::foo")
        session.visit("a.py::foo")
        assert session.depth == 1

    def test_session_to_dict(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.visit("a.py::foo")
        session.set_score("a.py", 0.8)
        d = session.to_dict()
        assert d["query"] == "query"
        assert "a.py::foo" in d["visited"]
        assert d["scores"]["a.py"] == 0.8
        assert d["depth"] == 1


class TestMCTSSessionManager:
    def test_create_and_retrieve(self):
        from mcts import MCTSSessionManager
        mgr = MCTSSessionManager()
        sid = mgr.create("query")
        session = mgr.get(sid)
        assert session is not None
        assert session.query == "query"

    def test_unknown_session_returns_none(self):
        from mcts import MCTSSessionManager
        mgr = MCTSSessionManager()
        assert mgr.get("nonexistent") is None

    def test_remove_session(self):
        from mcts import MCTSSessionManager
        mgr = MCTSSessionManager()
        sid = mgr.create("query")
        mgr.remove(sid)
        assert mgr.get(sid) is None

    def test_list_sessions(self):
        from mcts import MCTSSessionManager
        mgr = MCTSSessionManager()
        mgr.create("query1")
        mgr.create("query2")
        sessions = mgr.list_sessions()
        assert len(sessions) == 2
        queries = {s["query"] for s in sessions}
        assert queries == {"query1", "query2"}
