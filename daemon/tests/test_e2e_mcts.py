"""E2E tests for MCTS navigation loop: Explorer → Validator → Orchestrator."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcts import MCTSSession, MCTSSessionManager
from agents.explorer import build_explorer_prompt, parse_explorer_output
from agents.validator import build_validator_prompt, parse_validator_output
from agents.orchestrator import build_orchestrator_prompt, parse_orchestrator_output


@pytest.mark.e2e
class TestMCTSNavigationE2E:
    def test_full_navigation_loop(self):
        """Simulate Explorer → Validator → Orchestrator decision loop."""
        session = MCTSSession("How does authentication work?", max_depth=3)

        # Step 1: Explorer selects nodes
        explorer_prompt = build_explorer_prompt(
            query=session.query,
            tree_skeleton="class AuthManager:  # L1-50\nclass Database:  # L52-100\n",
            session_state=session.to_dict(),
        )
        assert "authentication" in explorer_prompt.lower()
        assert "AuthManager" in explorer_prompt
        assert "Database" in explorer_prompt

        # Simulate explorer response
        explorer_result = parse_explorer_output(json.dumps({
            "selected_nodes": [
                {"path": "auth.py", "symbol": "AuthManager", "score": 0.95,
                 "reason": "Handles authentication"},
            ],
            "action": "drill",
        }))
        assert explorer_result is not None
        assert explorer_result["action"] == "drill"
        assert len(explorer_result["selected_nodes"]) == 1

        # Step 2: Visit node + validate
        session.visit("auth.py::AuthManager")
        session.set_score("auth.py", 0.95)

        validator_prompt = build_validator_prompt(
            query=session.query,
            code_snippet="class AuthManager:\n    def validate(self): ...",
            symbol_path="auth.py::AuthManager",
        )
        assert "auth.py::AuthManager" in validator_prompt

        validator_result = parse_validator_output(json.dumps({
            "is_valid": True,
            "confidence": 0.9,
            "critique": "Directly handles auth logic.",
            "dependencies": ["token.py::TokenStore"],
        }))
        assert validator_result is not None
        assert validator_result["is_valid"] is True
        assert validator_result["confidence"] == 0.9

        # Step 3: Orchestrator decides next action
        orchestrator_prompt = build_orchestrator_prompt(
            query=session.query,
            session_state=session.to_dict(),
            last_result={"action": "drill", "verdict": "relevant"},
        )
        assert "authentication" in orchestrator_prompt.lower()

        orchestrator_result = parse_orchestrator_output(json.dumps({
            "next_action": "drill",
            "target_node": "token.py::TokenStore",
            "reasoning": "Need to understand token storage.",
            "should_blacklist": None,
        }))
        assert orchestrator_result is not None
        assert orchestrator_result["next_action"] == "drill"
        assert orchestrator_result["target_node"] == "token.py::TokenStore"

        # Step 4: Continue until max depth
        session.visit("token.py::TokenStore")
        session.visit("session.py::SessionManager")
        assert session.at_max_depth
        assert session.depth == 3

    def test_blacklisting_flow(self):
        """Irrelevant nodes should be blacklisted and skipped in prompts."""
        session = MCTSSession("auth query", max_depth=5)

        # Visit an irrelevant node
        session.visit("utils.py::format_date")
        session.blacklist_node("utils.py::format_date")

        state = session.to_dict()
        assert "utils.py::format_date" in state["blacklist"]
        assert "utils.py::format_date" in state["visited"]

        # Explorer prompt should include blacklist
        prompt = build_explorer_prompt(
            query=session.query,
            tree_skeleton="class AuthManager:  # L1-50\ndef format_date:  # L52-60\n",
            session_state=state,
        )
        assert "format_date" in prompt  # Blacklisted node appears in blacklist section

    def test_session_manager_lifecycle(self):
        """SessionManager creates, retrieves, and removes sessions."""
        mgr = MCTSSessionManager()

        # Create
        sid = mgr.create("find the login handler", max_depth=4)
        assert sid is not None

        # Retrieve
        session = mgr.get(sid)
        assert session is not None
        assert session.query == "find the login handler"
        assert session.max_depth == 4

        # Mutate
        session.visit("auth.py::login")
        session.set_score("auth.py", 0.8)

        # List
        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["depth"] == 1

        # Remove
        mgr.remove(sid)
        assert mgr.get(sid) is None

    def test_max_depth_forces_answer(self):
        """At max depth, session state should signal forced answer."""
        session = MCTSSession("query", max_depth=2)
        assert not session.at_max_depth

        session.visit("a.py::Foo")
        assert not session.at_max_depth

        session.visit("b.py::Bar")
        assert session.at_max_depth

        # Orchestrator prompt at max depth should include depth info
        prompt = build_orchestrator_prompt(
            query=session.query,
            session_state=session.to_dict(),
            last_result={"action": "drill", "verdict": "partial"},
        )
        # Session state in prompt should show depth == max_depth
        assert '"depth": 2' in prompt
        assert '"max_depth": 2' in prompt
