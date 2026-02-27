import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestExplorerPrompt:
    def test_builds_valid_prompt(self):
        from agents.explorer import build_explorer_prompt
        prompt = build_explorer_prompt(
            query="How does authentication work?",
            tree_skeleton="class Auth:\n  def validate(): ...\nclass DB:\n  def connect(): ...",
            session_state={"visited": [], "blacklist": [], "depth": 0},
        )
        assert "authentication" in prompt.lower()
        assert "Auth" in prompt
        assert isinstance(prompt, str)

    def test_respects_blacklist(self):
        from agents.explorer import build_explorer_prompt
        prompt = build_explorer_prompt(
            query="query",
            tree_skeleton="class A:\nclass B:",
            session_state={"visited": [], "blacklist": ["A"], "depth": 0},
        )
        assert "blacklist" in prompt.lower() or "skip" in prompt.lower()

    def test_includes_depth(self):
        from agents.explorer import build_explorer_prompt
        prompt = build_explorer_prompt(
            query="query",
            tree_skeleton="class A:",
            session_state={"visited": ["a.py::foo"], "blacklist": [], "depth": 3},
        )
        assert "3" in prompt


class TestValidatorPrompt:
    def test_builds_valid_prompt(self):
        from agents.validator import build_validator_prompt
        prompt = build_validator_prompt(
            query="How does auth work?",
            code_snippet="def validate_token(token):\n    return jwt.decode(token, KEY)",
            symbol_path="auth.py::validate_token",
        )
        assert "validate_token" in prompt
        assert "auth" in prompt.lower()
        assert isinstance(prompt, str)


class TestOrchestratorPrompt:
    def test_builds_valid_prompt(self):
        from agents.orchestrator import build_orchestrator_prompt
        prompt = build_orchestrator_prompt(
            query="How does auth work?",
            session_state={"visited": ["auth.py::validate"], "blacklist": [], "depth": 1, "scores": {}},
            last_result={"action": "drill", "symbol": "validate_token", "verdict": "relevant"},
        )
        assert "auth" in prompt.lower()
        assert isinstance(prompt, str)

    def test_includes_session_state(self):
        from agents.orchestrator import build_orchestrator_prompt
        prompt = build_orchestrator_prompt(
            query="query",
            session_state={"visited": ["x.py::bar"], "blacklist": ["y.py"], "depth": 2, "scores": {"x.py": 0.9}},
            last_result={"action": "drill"},
        )
        assert "x.py::bar" in prompt
        assert "y.py" in prompt


class TestOutputParsing:
    def test_parse_explorer_output(self):
        from agents.explorer import parse_explorer_output
        raw = '{"selected_nodes": [{"path": "src/auth.py", "symbol": "validate", "score": 0.9, "reason": "Handles auth"}], "action": "drill"}'
        result = parse_explorer_output(raw)
        assert result is not None
        assert len(result["selected_nodes"]) == 1
        assert result["action"] == "drill"

    def test_parse_explorer_output_with_markdown(self):
        from agents.explorer import parse_explorer_output
        raw = '```json\n{"selected_nodes": [], "action": "answer"}\n```'
        result = parse_explorer_output(raw)
        assert result is not None
        assert result["action"] == "answer"

    def test_parse_explorer_output_invalid(self):
        from agents.explorer import parse_explorer_output
        assert parse_explorer_output("not json") is None

    def test_parse_validator_output(self):
        from agents.validator import parse_validator_output
        raw = '{"is_valid": true, "confidence": 0.95, "critique": "Directly handles JWT validation."}'
        result = parse_validator_output(raw)
        assert result is not None
        assert result["is_valid"] is True
        assert result["confidence"] == 0.95

    def test_parse_orchestrator_output(self):
        from agents.orchestrator import parse_orchestrator_output
        raw = '{"next_action": "drill", "target_node": "src/auth.py::refresh_token", "reasoning": "Need to check token refresh."}'
        result = parse_orchestrator_output(raw)
        assert result is not None
        assert result["next_action"] == "drill"
        assert result["target_node"] == "src/auth.py::refresh_token"
