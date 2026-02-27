"""E2E tests for code and document navigation flows."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm_daemon import SkeletonCache, handle_request


def _request(action_dict: dict, cache: SkeletonCache, root: str) -> dict:
    """Helper: send a request dict through handle_request and return parsed response."""
    resp_bytes = handle_request(json.dumps(action_dict).encode(), cache, root)
    return json.loads(resp_bytes.decode("utf-8"))


@pytest.mark.e2e
class TestCodeNavigationE2E:
    def test_tree_map_drill_search_cycle(self, tmp_path):
        """Full navigation: tree → map → drill → search."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "auth.py").write_text(
            "class AuthManager:\n"
            "    def validate_token(self, token: str) -> bool:\n"
            "        return token.startswith('sk-')\n"
            "\n"
            "    def refresh_token(self, token: str) -> str:\n"
            "        return 'new-' + token\n"
        )
        (src / "db.py").write_text(
            "class Database:\n"
            "    def connect(self):\n"
            "        pass\n"
        )

        cache = SkeletonCache()
        root = str(tmp_path)

        # Step 1: Tree — verify directory structure
        resp = _request({"action": "tree"}, cache, root)
        assert "tree" in resp
        names = [e["name"] for e in resp["tree"]]
        assert "src" in names

        # Step 2: Map — squeeze a file to get skeleton
        resp = _request({"action": "squeeze", "path": "src/auth.py"}, cache, root)
        assert "skeleton" in resp
        assert "AuthManager" in resp["skeleton"]
        assert "validate_token" in resp["skeleton"]
        assert "refresh_token" in resp["skeleton"]

        # Step 3: Drill — find a specific symbol
        resp = _request(
            {"action": "find", "path": "src/auth.py", "symbol": "validate_token"},
            cache, root,
        )
        assert "start_line" in resp
        assert "end_line" in resp
        assert resp["start_line"] < resp["end_line"]

        # Step 4: Search — find symbol across files
        resp = _request({"action": "search", "query": "AuthManager"}, cache, root)
        assert "results" in resp
        assert len(resp["results"]) >= 1
        paths = [r["path"] for r in resp["results"]]
        assert any("auth.py" in p for p in paths)

    def test_squeeze_multiple_languages(self, tmp_path):
        """Squeeze should work for Python, JavaScript, and TypeScript."""
        (tmp_path / "app.py").write_text(
            "class App:\n"
            "    def run(self):\n"
            "        pass\n"
        )
        (tmp_path / "service.js").write_text(
            "class Service {\n"
            "    constructor() {}\n"
            "    start() { return true; }\n"
            "}\n"
        )
        (tmp_path / "types.ts").write_text(
            "interface Config {\n"
            "    host: string;\n"
            "    port: number;\n"
            "}\n"
        )

        cache = SkeletonCache()
        root = str(tmp_path)

        py = _request({"action": "squeeze", "path": "app.py"}, cache, root)
        assert "App" in py["skeleton"]

        js = _request({"action": "squeeze", "path": "service.js"}, cache, root)
        assert "Service" in js["skeleton"]

        ts = _request({"action": "squeeze", "path": "types.ts"}, cache, root)
        assert "Config" in ts["skeleton"]


@pytest.mark.e2e
class TestDocumentNavigationE2E:
    def test_doc_map_drill_assess_cycle(self, tmp_path):
        """Full document navigation: squeeze → doc_map → doc_drill → assess."""
        (tmp_path / "README.md").write_text(
            "# Project Overview\n\n"
            "This is a test project.\n\n"
            "## Installation\n\n"
            "Run `pip install` to install.\n\n"
            "### Prerequisites\n\n"
            "You need Python 3.10+.\n\n"
            "## Usage\n\n"
            "Import and call `main()`.\n"
        )

        cache = SkeletonCache()
        root = str(tmp_path)

        # Squeeze produces document skeleton
        resp = _request({"action": "squeeze", "path": "README.md"}, cache, root)
        assert "skeleton" in resp
        assert "Installation" in resp["skeleton"]

        # doc_map returns structured tree
        resp = _request({"action": "doc_map", "path": "README.md"}, cache, root)
        assert "tree" in resp
        tree = resp["tree"]
        assert tree["type"] == "document"

        # Find section names in children
        def collect_names(node):
            names = []
            for child in node.get("children", []):
                names.append(child["name"])
                names.extend(collect_names(child))
            return names

        all_names = collect_names(tree)
        assert "Project Overview" in all_names
        assert "Installation" in all_names
        assert "Usage" in all_names

        # doc_drill extracts specific section
        resp = _request(
            {"action": "doc_drill", "path": "README.md", "section": "Installation"},
            cache, root,
        )
        assert "content" in resp
        assert "pip install" in resp["content"]

        # assess provides guidance
        resp = _request(
            {
                "action": "assess",
                "query": "How do I install?",
                "context_summary": "Found Installation section with pip install instructions",
            },
            cache, root,
        )
        assert "assessment" in resp
        assert "install" in resp["assessment"].lower()
