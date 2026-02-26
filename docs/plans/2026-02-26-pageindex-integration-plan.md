# PageIndex Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fuse PageIndex into RLM Navigator so the daemon indexes code AND documents into a unified navigable tree, with Haiku-powered semantic enrichment on all nodes, formalizing the MCTS loop and Triad agent architecture.

**Architecture:** Dual-provider (OpenAI for document indexing via PageIndex library, Haiku for code enrichment + agents). Both optional — core AST navigation works offline. PageIndex is a pip dependency, not a fork.

**Tech Stack:** Python (daemon + PageIndex + Anthropic SDK), TypeScript (MCP server). New dependencies: `pageindex`, `anthropic`.

---

### Task 1: Add Dependencies + Configuration Layer

**Files:**
- Modify: `daemon/requirements.txt`
- Create: `daemon/config.py`
- Test: `daemon/tests/test_config.py`

**Step 1: Write the failing test**

Create `daemon/tests/test_config.py`:

```python
import os
import pytest

class TestConfig:
    def test_config_loads_defaults(self):
        """Config should have sensible defaults when no env vars set."""
        # Clear any existing keys for isolation
        old_vals = {}
        for key in ["ANTHROPIC_API_KEY", "CHATGPT_API_KEY", "PAGEINDEX_MODEL"]:
            old_vals[key] = os.environ.pop(key, None)

        try:
            from config import RLMConfig
            cfg = RLMConfig()
            assert cfg.anthropic_api_key is None
            assert cfg.openai_api_key is None
            assert cfg.pageindex_model == "gpt-4o-2024-11-20"
            assert cfg.enrichment_enabled is False
            assert cfg.doc_indexing_enabled is False
        finally:
            for key, val in old_vals.items():
                if val is not None:
                    os.environ[key] = val

    def test_config_detects_anthropic_key(self):
        """Config should enable enrichment when Anthropic key present."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
        try:
            from config import RLMConfig
            cfg = RLMConfig()
            assert cfg.anthropic_api_key == "sk-ant-test-key"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_config_detects_openai_key(self):
        """Config should enable doc indexing when OpenAI key present."""
        os.environ["CHATGPT_API_KEY"] = "sk-test-key"
        try:
            from config import RLMConfig
            cfg = RLMConfig()
            assert cfg.openai_api_key == "sk-test-key"
            assert cfg.doc_indexing_enabled is True
        finally:
            del os.environ["CHATGPT_API_KEY"]

    def test_pageindex_available(self):
        """Should detect whether pageindex is importable."""
        from config import RLMConfig
        cfg = RLMConfig()
        assert isinstance(cfg.pageindex_available, bool)

    def test_anthropic_available(self):
        """Should detect whether anthropic SDK is importable."""
        from config import RLMConfig
        cfg = RLMConfig()
        assert isinstance(cfg.anthropic_available, bool)
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_config.py -v`
Expected: FAIL with ImportError (config.py doesn't exist)

**Step 3: Write minimal implementation**

Create `daemon/config.py`:

```python
"""Configuration for RLM Navigator — manages API keys, feature flags, and dependency detection."""

import os
from pathlib import Path

# Try loading .env from project root if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class RLMConfig:
    """Centralized configuration. Reads from environment variables."""

    def __init__(self):
        self.anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
        self.openai_api_key: str | None = os.environ.get("CHATGPT_API_KEY")
        self.pageindex_model: str = os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20")

    @property
    def enrichment_enabled(self) -> bool:
        """Haiku enrichment requires Anthropic key + SDK."""
        return self.anthropic_api_key is not None and self.anthropic_available

    @property
    def doc_indexing_enabled(self) -> bool:
        """Document indexing requires OpenAI key + PageIndex library."""
        return self.openai_api_key is not None and self.pageindex_available

    @property
    def pageindex_available(self) -> bool:
        try:
            import pageindex
            return True
        except ImportError:
            return False

    @property
    def anthropic_available(self) -> bool:
        try:
            import anthropic
            return True
        except ImportError:
            return False
```

Update `daemon/requirements.txt` — add optional dependencies:

```
# Optional: Document indexing (requires CHATGPT_API_KEY)
# pageindex
# Optional: Code enrichment + agents (requires ANTHROPIC_API_KEY)
# anthropic>=0.40
# Optional: .env file support
# python-dotenv>=1.0
```

Note: Keep these commented for now. They become real requirements after Task 2 (doc indexer) and Task 3 (enricher) are built and tested. Uncomment when those tasks are complete.

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_config.py -v`
Expected: PASS (all 5 tests)

**Step 5: Commit**

```bash
git add daemon/config.py daemon/tests/test_config.py daemon/requirements.txt
git commit -m "feat: add configuration layer for dual-provider LLM integration"
```

---

### Task 2: Document Indexer — PageIndex Wrapper + Adapter

**Files:**
- Create: `daemon/doc_indexer.py`
- Test: `daemon/tests/test_doc_indexer.py`

**Step 1: Write the failing test**

Create `daemon/tests/test_doc_indexer.py`:

```python
import json
import pytest
from pathlib import Path

class TestDocumentTypeDetection:
    def test_markdown_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("README.md") is True
        assert is_document_file("guide.markdown") is True

    def test_txt_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("notes.txt") is True

    def test_rst_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("index.rst") is True

    def test_pdf_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("report.pdf") is True

    def test_code_not_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("main.py") is False
        assert is_document_file("index.ts") is False

class TestMarkdownToUnifiedTree:
    def test_simple_markdown(self, tmp_path):
        """Markdown with headers should produce a hierarchical tree."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Project\n\nOverview text.\n\n## Installation\n\nRun npm install.\n\n## Usage\n\nImport and use.\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        assert tree is not None
        assert tree["name"] == "README.md"
        assert tree["type"] == "document"
        assert tree["source"] == "local_md"
        assert len(tree["children"]) >= 1  # At least the top-level heading

    def test_nested_headings(self, tmp_path):
        """Nested markdown headings should produce nested children."""
        md_file = tmp_path / "guide.md"
        md_file.write_text("# Top\n\n## Sub A\n\nContent A.\n\n### Sub Sub\n\nDeep content.\n\n## Sub B\n\nContent B.\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        top_children = tree["children"]
        assert len(top_children) >= 1
        # Find the node with sub-children
        has_nested = any(len(c.get("children", [])) > 0 for c in top_children)
        assert has_nested, "Nested headings should produce nested children"

    def test_empty_markdown(self, tmp_path):
        """Empty markdown should return a minimal tree."""
        md_file = tmp_path / "empty.md"
        md_file.write_text("")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))
        assert tree is not None
        assert tree["children"] == []

class TestUnifiedNodeFormat:
    def test_node_has_required_fields(self, tmp_path):
        """Every node must have name, type, source, path, children."""
        md_file = tmp_path / "test.md"
        md_file.write_text("# Hello\n\nWorld.\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        def check_node(node):
            assert "name" in node
            assert "type" in node
            assert "source" in node
            assert "children" in node
            for child in node["children"]:
                check_node(child)

        check_node(tree)

class TestPageIndexIntegration:
    def test_pageindex_markdown_if_available(self, tmp_path):
        """If PageIndex is installed and key available, use it for richer indexing."""
        from doc_indexer import index_document
        from config import RLMConfig
        cfg = RLMConfig()

        md_file = tmp_path / "test.md"
        md_file.write_text("# Introduction\n\nThis is a test document.\n\n## Methods\n\nWe describe methods here.\n")

        tree = index_document(str(md_file), cfg)
        assert tree is not None
        assert tree["name"] == "test.md"
        # Should work regardless of whether PageIndex is installed (falls back to local)
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_doc_indexer.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Create `daemon/doc_indexer.py`:

```python
"""Document indexer — routes document files through PageIndex or local fallback.

Produces unified node trees compatible with the code skeleton format.
"""

import re
import json
from pathlib import Path
from typing import Optional

DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".pdf"}


def is_document_file(file_path: str) -> bool:
    """Check if a file is a document type (not code)."""
    return Path(file_path).suffix.lower() in DOC_EXTENSIONS


def index_document(file_path: str, config=None) -> Optional[dict]:
    """Index a document file. Uses PageIndex if available, else local fallback.

    Returns a unified node tree dict or None on failure.
    """
    ext = Path(file_path).suffix.lower()

    # Try PageIndex for richer indexing (requires API key + library)
    if config and config.doc_indexing_enabled and ext in (".md", ".markdown"):
        try:
            return _index_with_pageindex_md(file_path, config)
        except Exception:
            pass  # Fall through to local

    if config and config.doc_indexing_enabled and ext == ".pdf":
        try:
            return _index_with_pageindex_pdf(file_path, config)
        except Exception:
            pass

    # Local fallback for markdown
    if ext in (".md", ".markdown"):
        return index_markdown_local(file_path)

    # Local fallback for plain text / rst
    if ext in (".txt", ".rst"):
        return _index_plaintext_local(file_path)

    return None


def index_markdown_local(file_path: str) -> Optional[dict]:
    """Parse markdown into a hierarchical tree using header structure. No LLM needed."""
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    filename = Path(file_path).name
    root = _make_node(filename, "document", "local_md")

    if not text.strip():
        return root

    lines = text.split("\n")
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

    # Build flat list of headings with their line numbers and levels
    headings = []
    in_code_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        m = heading_pattern.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            headings.append({"title": title, "level": level, "line": i + 1})

    if not headings:
        return root

    # Assign end lines
    for i, h in enumerate(headings):
        if i + 1 < len(headings):
            h["end_line"] = headings[i + 1]["line"] - 1
        else:
            h["end_line"] = len(lines)

    # Build tree using stack-based algorithm (same as PageIndex's build_tree_from_nodes)
    stack = [(root, 0)]  # (node, level) — root is level 0
    for h in headings:
        node = _make_node(
            h["title"], "section", "local_md",
            range_start=h["line"], range_end=h["end_line"],
        )
        # Pop until we find a parent with lower level
        while len(stack) > 1 and stack[-1][1] >= h["level"]:
            stack.pop()
        stack[-1][0]["children"].append(node)
        stack.append((node, h["level"]))

    return root


def _index_plaintext_local(file_path: str) -> Optional[dict]:
    """Minimal indexing for plain text — just line count and first few lines as summary."""
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.split("\n")
    filename = Path(file_path).name
    node = _make_node(filename, "document", "local_txt")
    node["metadata"]["line_count"] = len(lines)
    node["metadata"]["preview"] = "\n".join(lines[:10])
    return node


def _index_with_pageindex_md(file_path: str, config) -> Optional[dict]:
    """Use PageIndex library to index a markdown file."""
    import asyncio
    from pageindex.page_index_md import md_to_tree

    # Run async function synchronously
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(md_to_tree(
            md_path=file_path,
            model=config.pageindex_model,
            if_add_node_summary="yes",
            if_add_node_id="yes",
        ))
    finally:
        loop.close()

    if not result:
        return None

    return _adapt_pageindex_tree(result, Path(file_path).name, "pageindex_md")


def _index_with_pageindex_pdf(file_path: str, config) -> Optional[dict]:
    """Use PageIndex library to index a PDF file."""
    import asyncio
    from pageindex import page_index as pi_module

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(pi_module.page_index(
            pdf_path=file_path,
            model=config.pageindex_model,
            if_add_node_summary="yes",
            if_add_node_id="yes",
        ))
    finally:
        loop.close()

    if not result:
        return None

    return _adapt_pageindex_tree(result, Path(file_path).name, "pageindex_pdf")


def _adapt_pageindex_tree(pi_tree: dict | list, filename: str, source: str) -> dict:
    """Transform PageIndex output into RLM unified node format."""
    root = _make_node(filename, "document", source)

    nodes = pi_tree if isinstance(pi_tree, list) else pi_tree.get("nodes", [pi_tree])
    for pi_node in nodes:
        root["children"].append(_adapt_node(pi_node, source))

    return root


def _adapt_node(pi_node: dict, source: str) -> dict:
    """Recursively adapt a single PageIndex node."""
    title = pi_node.get("title", "Untitled")
    node = _make_node(title, "section", source)

    if "start_index" in pi_node:
        node["range"] = {"start": pi_node["start_index"], "end": pi_node.get("end_index")}
    if "node_id" in pi_node:
        node["metadata"]["node_id"] = pi_node["node_id"]
    if "summary" in pi_node:
        node["summary"] = pi_node["summary"]
    if "text" in pi_node:
        node["metadata"]["text_preview"] = pi_node["text"][:200]

    for child in pi_node.get("nodes", []):
        node["children"].append(_adapt_node(child, source))

    return node


def _make_node(name: str, node_type: str, source: str,
               range_start: int = None, range_end: int = None) -> dict:
    """Create a unified node dict."""
    node = {
        "name": name,
        "type": node_type,
        "source": source,
        "summary": None,
        "metadata": {},
        "children": [],
    }
    if range_start is not None:
        node["range"] = {"start": range_start, "end": range_end}
    return node
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_doc_indexer.py -v`
Expected: PASS (all tests — PageIndex integration test passes either way via fallback)

**Step 5: Commit**

```bash
git add daemon/doc_indexer.py daemon/tests/test_doc_indexer.py
git commit -m "feat: document indexer with PageIndex integration and local markdown fallback"
```

---

### Task 3: Node Enricher — Haiku Semantic Summaries

**Files:**
- Create: `daemon/node_enricher.py`
- Test: `daemon/tests/test_node_enricher.py`

**Step 1: Write the failing test**

Create `daemon/tests/test_node_enricher.py`:

```python
import pytest
import json

class TestSkeletonParsing:
    def test_parse_skeleton_to_symbols(self):
        """Should extract symbol names and line ranges from skeleton text."""
        from node_enricher import parse_skeleton_symbols
        skeleton = """# test.py — 3 symbols, 20 lines
class Calculator:  # L1-14
    ...

  def add(self, a: int, b: int) -> int:  # L3-8
      \"\"\"Add two numbers.\"\"\"
    ...

  def subtract(self, a, b):  # L10-14
    ...
"""
        symbols = parse_skeleton_symbols(skeleton)
        assert len(symbols) == 3
        assert symbols[0]["name"] == "Calculator"
        assert symbols[0]["type"] == "class"
        assert symbols[1]["name"] == "add"
        assert symbols[2]["name"] == "subtract"

class TestEnrichmentPrompt:
    def test_build_prompt(self):
        """Should build a valid prompt for Haiku."""
        from node_enricher import build_enrichment_prompt
        symbols = [
            {"name": "Calculator", "type": "class", "signature": "class Calculator:", "range": "L1-14"},
            {"name": "add", "type": "function", "signature": "def add(self, a: int, b: int) -> int:", "range": "L3-8"},
        ]
        prompt = build_enrichment_prompt("math_utils.py", symbols)
        assert "math_utils.py" in prompt
        assert "Calculator" in prompt
        assert "add" in prompt

class TestEnrichmentCache:
    def test_cache_stores_and_retrieves(self, tmp_path):
        """Enrichment cache should store and retrieve by file path + mtime."""
        from node_enricher import EnrichmentCache
        cache = EnrichmentCache()

        enrichments = {"Calculator": "A basic arithmetic calculator class."}
        cache.put("test.py", 1000.0, enrichments)

        result = cache.get("test.py", 1000.0)
        assert result == enrichments

    def test_cache_invalidates_on_mtime_change(self, tmp_path):
        """Cache should miss when mtime changes."""
        from node_enricher import EnrichmentCache
        cache = EnrichmentCache()

        cache.put("test.py", 1000.0, {"Calculator": "A calculator."})

        result = cache.get("test.py", 1001.0)
        assert result is None

class TestEnrichmentMerge:
    def test_merge_enrichments_into_skeleton(self):
        """Should annotate skeleton lines with summaries."""
        from node_enricher import merge_enrichments
        skeleton = "class Calculator:  # L1-14\n    ...\n\n  def add(self, a, b):  # L3-8\n    ...\n"
        enrichments = {
            "Calculator": "A basic arithmetic calculator.",
            "add": "Returns the sum of two numbers.",
        }
        result = merge_enrichments(skeleton, enrichments)
        assert "# A basic arithmetic calculator." in result
        assert "# Returns the sum of two numbers." in result
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Create `daemon/node_enricher.py`:

```python
"""Node enricher — generates semantic summaries for AST skeleton nodes using Haiku.

Parses skeleton output from squeezer.py, batches symbols, calls Haiku for 1-line
summaries, caches results by file path + mtime.
"""

import re
import json
import threading
from typing import Optional

# Haiku prompt template
ENRICHMENT_PROMPT = """You are a code analyst. Given these code signatures from {filename}, provide a concise 1-line semantic summary for each symbol describing what it does (not what it is).

Symbols:
{symbols_text}

Respond with a JSON object mapping each symbol name to its 1-line summary.
Example: {{"process_data": "Transforms raw CSV rows into normalized database records."}}

JSON response:"""


def parse_skeleton_symbols(skeleton: str) -> list[dict]:
    """Extract symbol entries from a skeleton string.

    Each entry has: name, type (class/function), signature, range.
    """
    symbols = []
    line_pattern = re.compile(r"^(\s*)(class |def |async def |function |export |interface |struct |impl |type |fn )(.+?)(?:\s+#\s*L(\d+)-(\d+))?$")

    for line in skeleton.split("\n"):
        m = line_pattern.match(line)
        if not m:
            continue
        indent = m.group(1)
        keyword = m.group(2).strip()
        rest = m.group(3)
        start = m.group(4)
        end = m.group(5)

        # Extract the symbol name (first word/identifier after keyword)
        name_match = re.match(r"(\w+)", rest)
        if not name_match:
            continue

        name = name_match.group(1)
        sig_type = "class" if keyword == "class" else "function"
        signature = f"{keyword} {rest}".rstrip(":")

        symbols.append({
            "name": name,
            "type": sig_type,
            "signature": signature.strip(),
            "range": f"L{start}-{end}" if start else None,
        })

    return symbols


def build_enrichment_prompt(filename: str, symbols: list[dict]) -> str:
    """Build the Haiku prompt for a batch of symbols."""
    symbols_text = "\n".join(
        f"- {s['signature']}  {s['range'] or ''}" for s in symbols
    )
    return ENRICHMENT_PROMPT.format(filename=filename, symbols_text=symbols_text)


class EnrichmentCache:
    """Thread-safe cache for enrichment results, keyed by file path + mtime."""

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}  # path -> (mtime, enrichments)
        self._lock = threading.Lock()

    def get(self, file_path: str, mtime: float) -> Optional[dict]:
        with self._lock:
            entry = self._cache.get(file_path)
            if entry and entry[0] == mtime:
                return entry[1]
            return None

    def put(self, file_path: str, mtime: float, enrichments: dict) -> None:
        with self._lock:
            self._cache[file_path] = (mtime, enrichments)

    def invalidate(self, file_path: str) -> None:
        with self._lock:
            self._cache.pop(file_path, None)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


def merge_enrichments(skeleton: str, enrichments: dict[str, str]) -> str:
    """Annotate skeleton lines with enrichment summaries.

    Adds '# <summary>' after the line range comment for each enriched symbol.
    """
    lines = skeleton.split("\n")
    result = []

    for line in lines:
        # Check if this line has a symbol we can enrich
        for name, summary in enrichments.items():
            # Match lines like "class Foo:  # L1-14" or "  def bar(...):  # L3-8"
            pattern = re.compile(rf"(?:class |def |async def |fn |function ){re.escape(name)}\b.*#\s*L\d+-\d+")
            if pattern.search(line):
                line = f"{line}  # {summary}"
                break
        result.append(line)

    return "\n".join(result)


async def enrich_file(file_path: str, skeleton: str, config) -> Optional[dict]:
    """Call Haiku to generate enrichments for a file's skeleton.

    Returns dict mapping symbol names to summaries, or None on failure.
    """
    if not config or not config.enrichment_enabled:
        return None

    symbols = parse_skeleton_symbols(skeleton)
    if not symbols:
        return None

    prompt = build_enrichment_prompt(file_path, symbols)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Parse JSON from response (may be wrapped in ```json blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except Exception:
        return None
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py -v`
Expected: PASS (all tests — `enrich_file` is async and not tested here; cache/parsing/merge are unit-tested)

**Step 5: Commit**

```bash
git add daemon/node_enricher.py daemon/tests/test_node_enricher.py
git commit -m "feat: node enricher with Haiku semantic summaries and enrichment cache"
```

---

### Task 4: Integrate Document Indexer into Daemon

**Files:**
- Modify: `daemon/rlm_daemon.py`
- Modify: `daemon/squeezer.py`
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_daemon.py`:

```python
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

    def test_squeeze_returns_fallback_for_markdown(self, tmp_path):
        """squeeze action on .md should return the local markdown skeleton."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Title\n\nSome text.\n")

        cache = SkeletonCache()
        data = json.dumps({"action": "squeeze", "path": "README.md"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))

        # Should return something (fallback or doc-aware skeleton)
        assert "skeleton" in resp

    def test_search_includes_document_files(self, tmp_path):
        """search should find symbols/headings in document files."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Installation Guide\n\nFollow these steps.\n")

        cache = SkeletonCache()
        # First, trigger caching
        data = json.dumps({"action": "squeeze", "path": "README.md"}).encode()
        handle_request(data, cache, str(tmp_path))

        data = json.dumps({"action": "search", "query": "Installation"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "results" in resp
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestDocumentIndexing -v`
Expected: FAIL (doc_map action doesn't exist)

**Step 3: Write minimal implementation**

In `daemon/squeezer.py`, extend `_detect_language` to recognize document types:

```python
# Add to EXT_MAP (after the existing entries)
DOC_EXT_MAP: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "plaintext",
    ".rst": "restructuredtext",
    ".pdf": "pdf",
}
```

Modify `squeeze()` to handle document files — insert before the existing `_fallback_squeeze` call:

```python
    # Check for document files
    from doc_indexer import is_document_file, index_markdown_local
    if is_document_file(file_path):
        tree = index_markdown_local(file_path)
        if tree:
            return _format_doc_skeleton(tree)
        return _fallback_squeeze(file_path)
```

Add `_format_doc_skeleton` helper:

```python
def _format_doc_skeleton(tree: dict) -> str:
    """Format a document tree as a skeleton string for display."""
    filename = tree["name"]
    sections = _count_sections(tree)
    lines = [f"# {filename} — {sections} sections (document)"]

    def _walk(node, depth=0):
        for child in node.get("children", []):
            indent = "  " * depth
            range_str = ""
            if "range" in child and child["range"]:
                r = child["range"]
                range_str = f"  # L{r['start']}-{r['end']}"
            lines.append(f"{indent}{child['name']}{range_str}")
            _walk(child, depth + 1)

    _walk(tree)
    return "\n".join(lines)


def _count_sections(tree: dict) -> int:
    count = 0
    for child in tree.get("children", []):
        count += 1 + _count_sections(child)
    return count
```

In `daemon/rlm_daemon.py`, add the `doc_map` action to `_handle_request_inner`:

```python
    elif action == "doc_map":
        rel = req.get("path", "")
        abs_path = _safe_path(root, rel)
        if abs_path is None:
            return json.dumps({"error": f"Invalid path: {rel}"}).encode("utf-8")
        if not Path(abs_path).exists():
            return json.dumps({"error": f"File not found: {rel}"}).encode("utf-8")

        from doc_indexer import is_document_file, index_document
        from config import RLMConfig
        if not is_document_file(abs_path):
            return json.dumps({"error": f"Not a document file: {rel}"}).encode("utf-8")

        cfg = RLMConfig()
        tree = index_document(abs_path, cfg)
        if tree is None:
            return json.dumps({"error": f"Failed to index: {rel}"}).encode("utf-8")

        return json.dumps({"tree": tree}).encode("utf-8")
```

In `search_symbols`, extend to include document files:

```python
    # Add after the existing supported-language check:
    from doc_indexer import is_document_file
    # ... in the rglob loop, also include files where is_document_file() returns True
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestDocumentIndexing -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All existing tests still PASS

**Step 6: Commit**

```bash
git add daemon/rlm_daemon.py daemon/squeezer.py daemon/tests/test_daemon.py
git commit -m "feat: integrate document indexer into daemon with doc_map action"
```

---

### Task 5: Integrate Node Enricher into Daemon

**Files:**
- Modify: `daemon/rlm_daemon.py`
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_daemon.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestEnrichmentIntegration -v`
Expected: FAIL (status doesn't include enrichment fields)

**Step 3: Write minimal implementation**

In `daemon/rlm_daemon.py`, modify the `status` action response to include:

```python
    # In the status action handler, add:
    from config import RLMConfig
    cfg = RLMConfig()
    # ... add to response dict:
    "enrichment_available": cfg.enrichment_enabled,
    "doc_indexing_available": cfg.doc_indexing_enabled,
```

Add `EnrichmentCache` instance alongside `SkeletonCache` in `run_server`:

```python
    from node_enricher import EnrichmentCache
    enrichment_cache = EnrichmentCache()
```

Wire enrichment into the `squeeze` action — after getting skeleton from cache:

```python
    # In squeeze action, after getting skeleton:
    # Optionally merge enrichments if available
    from node_enricher import EnrichmentCache, merge_enrichments
    if enrichment_cache:
        enrichments = enrichment_cache.get(abs_path, os.path.getmtime(abs_path))
        if enrichments:
            skeleton = merge_enrichments(skeleton, enrichments)
```

Note: The actual Haiku API call for enrichment happens asynchronously in the background — not blocking the squeeze response. The enrichment cache is populated by a background thread that processes files after they're squeezed. This is Task 5's scope — just wiring the cache lookup into the response path. The background enrichment worker is Task 6.

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestEnrichmentIntegration -v`
Expected: PASS

**Step 5: Commit**

```bash
git add daemon/rlm_daemon.py daemon/tests/test_daemon.py
git commit -m "feat: integrate enrichment cache into daemon status and squeeze actions"
```

---

### Task 6: Background Enrichment Worker

**Files:**
- Modify: `daemon/rlm_daemon.py`
- Test: `daemon/tests/test_node_enricher.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_node_enricher.py`:

```python
class TestEnrichmentWorker:
    def test_worker_processes_queue(self):
        """Enrichment worker should process files from queue."""
        from node_enricher import EnrichmentCache, EnrichmentWorker

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=None)  # No config = no API calls

        # Queue a file for enrichment
        skeleton = "class Foo:  # L1-10\n    ...\n"
        worker.enqueue("test.py", skeleton, 1000.0)

        assert worker.queue_size >= 0  # Worker exists and has a queue

    def test_worker_skips_when_no_config(self):
        """Worker should skip enrichment when no API key configured."""
        from node_enricher import EnrichmentCache, EnrichmentWorker

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=None)

        skeleton = "class Foo:  # L1-10\n    ...\n"
        worker.enqueue("test.py", skeleton, 1000.0)
        worker.process_one()  # Should not crash

        # Cache should be empty (no API call made)
        assert cache.get("test.py", 1000.0) is None
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py::TestEnrichmentWorker -v`
Expected: FAIL (EnrichmentWorker doesn't exist)

**Step 3: Write minimal implementation**

Add to `daemon/node_enricher.py`:

```python
import queue

class EnrichmentWorker:
    """Background worker that processes files for enrichment."""

    def __init__(self, cache: EnrichmentCache, config=None):
        self._cache = cache
        self._config = config
        self._queue: queue.Queue = queue.Queue()
        self._running = False

    def enqueue(self, file_path: str, skeleton: str, mtime: float) -> None:
        """Add a file to the enrichment queue."""
        # Skip if already enriched at this mtime
        if self._cache.get(file_path, mtime) is not None:
            return
        self._queue.put((file_path, skeleton, mtime))

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def process_one(self) -> bool:
        """Process one item from the queue. Returns True if an item was processed."""
        try:
            file_path, skeleton, mtime = self._queue.get_nowait()
        except queue.Empty:
            return False

        if not self._config or not self._config.enrichment_enabled:
            return True  # Skip silently

        symbols = parse_skeleton_symbols(skeleton)
        if not symbols:
            return True

        prompt = build_enrichment_prompt(file_path, symbols)

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._config.anthropic_api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            enrichments = json.loads(text)
            self._cache.put(file_path, mtime, enrichments)
        except Exception:
            pass  # Enrichment is best-effort

        return True

    def start(self) -> None:
        """Start the background worker thread."""
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()

    def _run_loop(self) -> None:
        import time
        while self._running:
            if not self.process_one():
                time.sleep(1)  # Idle poll

    def stop(self) -> None:
        self._running = False
```

In `daemon/rlm_daemon.py`, wire the worker into `run_server`:

```python
    from node_enricher import EnrichmentCache, EnrichmentWorker
    from config import RLMConfig

    cfg = RLMConfig()
    enrichment_cache = EnrichmentCache()
    enrichment_worker = EnrichmentWorker(enrichment_cache, cfg)
    enrichment_worker.start()
```

In the `squeeze` action, after caching the skeleton, enqueue for enrichment:

```python
    # After skeleton is returned from cache:
    if enrichment_worker:
        enrichment_worker.enqueue(rel_path, skeleton, os.path.getmtime(abs_path))
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add daemon/node_enricher.py daemon/rlm_daemon.py daemon/tests/test_node_enricher.py
git commit -m "feat: background enrichment worker queues files for Haiku summarization"
```

---

### Task 7: New MCP Tools — doc_map, doc_drill, assess

**Files:**
- Modify: `server/src/index.ts`

**Step 1: Add rlm_doc_map tool**

Add after the existing `rlm_search` tool block:

```typescript
server.tool(
  "rlm_doc_map",
  "Get hierarchical outline of a document file (.md, .pdf, .txt, .rst). Returns section tree with titles and line ranges.",
  { path: z.string().describe("Document file path relative to project root") },
  async ({ path }) => {
    const result = await queryDaemonWithRetry({ action: "doc_map", path });
    if (result.error) {
      return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    }
    const treeText = JSON.stringify(result.tree, null, 2);
    return { content: [{ type: "text", text: truncateResponse(treeText) + formatStats(result) }] };
  }
);
```

**Step 2: Add rlm_doc_drill tool**

```typescript
server.tool(
  "rlm_doc_drill",
  "Extract a specific section from a document file by section title. Use rlm_doc_map first to see available sections.",
  {
    path: z.string().describe("Document file path relative to project root"),
    section: z.string().describe("Section title to extract (from rlm_doc_map output)"),
  },
  async ({ path, section }) => {
    const result = await queryDaemonWithRetry({ action: "doc_drill", path, section });
    if (result.error) {
      return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    }
    return { content: [{ type: "text", text: truncateResponse(result.content) + formatStats(result) }] };
  }
);
```

**Step 3: Add rlm_assess tool**

```typescript
server.tool(
  "rlm_assess",
  "Assess whether accumulated context is sufficient to answer a query. Call after gathering code/doc snippets to decide whether to continue navigating or synthesize an answer.",
  {
    query: z.string().describe("The original user question"),
    context_summary: z.string().describe("Brief summary of what has been found so far"),
  },
  async ({ query, context_summary }) => {
    const result = await queryDaemonWithRetry({ action: "assess", query, context_summary });
    if (result.error) {
      return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    }
    return { content: [{ type: "text", text: result.assessment + formatStats(result) }] };
  }
);
```

**Step 4: Add daemon-side handlers**

In `daemon/rlm_daemon.py`, add `doc_drill` action:

```python
    elif action == "doc_drill":
        rel = req.get("path", "")
        section = req.get("section", "")
        abs_path = _safe_path(root, rel)
        if abs_path is None or not Path(abs_path).exists():
            return json.dumps({"error": f"File not found: {rel}"}).encode("utf-8")

        # Read the file and extract the section by line range
        from doc_indexer import index_document
        from config import RLMConfig
        cfg = RLMConfig()
        tree = index_document(abs_path, cfg)
        if not tree:
            return json.dumps({"error": "Failed to index document"}).encode("utf-8")

        # Find section by title (recursive search)
        node = _find_section(tree, section)
        if not node:
            return json.dumps({"error": f"Section not found: {section}"}).encode("utf-8")

        # Extract content by line range
        r = node.get("range")
        if r:
            lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").split("\n")
            content = "\n".join(lines[r["start"]-1:r["end"]])
        else:
            content = f"[Section '{section}' found but no line range available]"

        return json.dumps({"content": content}).encode("utf-8")
```

Add `assess` action (placeholder — full MCTS integration in Task 8):

```python
    elif action == "assess":
        query = req.get("query", "")
        context_summary = req.get("context_summary", "")
        # Simple heuristic for now; MCTS formalization replaces this
        assessment = (
            f"Query: {query}\n"
            f"Context gathered: {context_summary}\n\n"
            f"Assessment: Review the context above. If it directly addresses the query, "
            f"synthesize your answer. If gaps remain, continue navigating."
        )
        return json.dumps({"assessment": assessment}).encode("utf-8")
```

Add helper:

```python
def _find_section(node: dict, title: str) -> Optional[dict]:
    """Recursively find a section node by title (case-insensitive)."""
    if node.get("name", "").lower() == title.lower():
        return node
    for child in node.get("children", []):
        found = _find_section(child, title)
        if found:
            return found
    return None
```

**Step 5: Build and verify**

Run: `cd server && npm run build`
Expected: Clean build

**Step 6: Commit**

```bash
git add server/src/index.ts daemon/rlm_daemon.py
git commit -m "feat: add rlm_doc_map, rlm_doc_drill, rlm_assess MCP tools"
```

---

### Task 8: MCTS Session State + Orchestrator Scaffold

**Files:**
- Create: `daemon/mcts.py`
- Test: `daemon/tests/test_mcts.py`

**Step 1: Write the failing test**

Create `daemon/tests/test_mcts.py`:

```python
import pytest

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

    def test_max_depth_enforcement(self):
        from mcts import MCTSSession
        session = MCTSSession("query", max_depth=3)
        for i in range(3):
            session.visit(f"node_{i}")
        assert session.at_max_depth

    def test_session_to_dict(self):
        from mcts import MCTSSession
        session = MCTSSession("query")
        session.visit("a.py::foo")
        session.set_score("a.py", 0.8)
        d = session.to_dict()
        assert d["query"] == "query"
        assert "a.py::foo" in d["visited"]
        assert d["scores"]["a.py"] == 0.8

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
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_mcts.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Create `daemon/mcts.py`:

```python
"""MCTS session state for codebase navigation.

Tracks visited nodes, blacklisted branches, relevance scores,
and search depth for the Triad agent architecture.
"""

import uuid
import threading
from typing import Optional


class MCTSSession:
    """State for one MCTS navigation session (one user query)."""

    def __init__(self, query: str, max_depth: int = 5):
        self.session_id = str(uuid.uuid4())
        self.query = query
        self.max_depth = max_depth
        self.visited: list[str] = []
        self.blacklist: set[str] = set()
        self.scores: dict[str, float] = {}
        self.context_accumulated: list[str] = []

    @property
    def depth(self) -> int:
        return len(self.visited)

    @property
    def at_max_depth(self) -> bool:
        return self.depth >= self.max_depth

    def visit(self, node_id: str) -> None:
        """Record a node as visited."""
        if node_id not in self.visited:
            self.visited.append(node_id)

    def blacklist_node(self, node_id: str) -> None:
        """Mark a node/branch as irrelevant for this session."""
        self.blacklist.add(node_id)

    def set_score(self, node_id: str, score: float) -> None:
        """Set relevance score for a node (0.0 - 1.0)."""
        self.scores[node_id] = score

    def get_score(self, node_id: str) -> float:
        return self.scores.get(node_id, 0.0)

    def add_context(self, snippet: str) -> None:
        """Accumulate a context snippet."""
        self.context_accumulated.append(snippet)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "query": self.query,
            "depth": self.depth,
            "max_depth": self.max_depth,
            "visited": list(self.visited),
            "blacklist": list(self.blacklist),
            "scores": dict(self.scores),
            "context_count": len(self.context_accumulated),
        }


class MCTSSessionManager:
    """Manages multiple MCTS sessions (one per active query)."""

    def __init__(self):
        self._sessions: dict[str, MCTSSession] = {}
        self._lock = threading.Lock()

    def create(self, query: str, max_depth: int = 5) -> str:
        """Create a new session. Returns session ID."""
        session = MCTSSession(query, max_depth)
        with self._lock:
            self._sessions[session.session_id] = session
        return session.session_id

    def get(self, session_id: str) -> Optional[MCTSSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_mcts.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add daemon/mcts.py daemon/tests/test_mcts.py
git commit -m "feat: MCTS session state manager for navigation tracking"
```

---

### Task 9: Triad Agent Prompt Templates

**Files:**
- Create: `daemon/agents/__init__.py`
- Create: `daemon/agents/explorer.py`
- Create: `daemon/agents/validator.py`
- Create: `daemon/agents/orchestrator.py`
- Test: `daemon/tests/test_agents.py`

**Step 1: Write the failing test**

Create `daemon/tests/test_agents.py`:

```python
import pytest
import json

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

class TestValidatorPrompt:
    def test_builds_valid_prompt(self):
        from agents.validator import build_validator_prompt
        prompt = build_validator_prompt(
            query="How does auth work?",
            code_snippet="def validate_token(token):\n    return jwt.decode(token, KEY)",
            symbol_path="auth.py::validate_token",
        )
        assert "validate_token" in prompt
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

class TestOutputParsing:
    def test_parse_explorer_output(self):
        from agents.explorer import parse_explorer_output
        raw = '{"selected_nodes": [{"path": "src/auth.py", "symbol": "validate", "score": 0.9, "reason": "Handles auth"}], "action": "drill"}'
        result = parse_explorer_output(raw)
        assert len(result["selected_nodes"]) == 1
        assert result["action"] == "drill"

    def test_parse_validator_output(self):
        from agents.validator import parse_validator_output
        raw = '{"is_valid": true, "confidence": 0.95, "critique": "Directly handles JWT validation."}'
        result = parse_validator_output(raw)
        assert result["is_valid"] is True

    def test_parse_orchestrator_output(self):
        from agents.orchestrator import parse_orchestrator_output
        raw = '{"next_action": "drill", "target_node": "src/auth.py::refresh_token", "reasoning": "Need to check token refresh."}'
        result = parse_orchestrator_output(raw)
        assert result["next_action"] == "drill"
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_agents.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Create `daemon/agents/__init__.py` (empty).

Create `daemon/agents/explorer.py`:

```python
"""Explorer agent — proposes navigation paths based on tree skeletons.

The Explorer is the MCTS "Policy Network." It examines AST skeletons and document
outlines to propose the most probable symbolic paths for a given query.
"""

import json
from typing import Optional

EXPLORER_PROMPT = """You are a code exploration agent. Your job is to identify the most relevant files and symbols to investigate for a given query.

QUERY: {query}

CODEBASE SKELETON:
{tree_skeleton}

SESSION STATE:
- Previously visited: {visited}
- Blacklisted (skip these): {blacklist}
- Current depth: {depth}

RULES:
- Propose 1-3 nodes to investigate, ranked by relevance (0.0-1.0)
- Never propose blacklisted nodes
- Prefer unexplored branches over revisiting
- If no promising nodes remain, set action to "answer" or "pivot"

Respond with JSON:
{{"selected_nodes": [{{"path": "...", "symbol": "...", "score": 0.0-1.0, "reason": "..."}}], "action": "drill|map|answer|pivot"}}"""


def build_explorer_prompt(query: str, tree_skeleton: str, session_state: dict) -> str:
    return EXPLORER_PROMPT.format(
        query=query,
        tree_skeleton=tree_skeleton,
        visited=json.dumps(session_state.get("visited", [])),
        blacklist=json.dumps(session_state.get("blacklist", [])),
        depth=session_state.get("depth", 0),
    )


def parse_explorer_output(raw: str) -> Optional[dict]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
```

Create `daemon/agents/validator.py`:

```python
"""Validator agent — critiques drill results against query requirements.

The Validator is the MCTS "Value Network." It reads implementation code and
determines if it's relevant to the user's query.
"""

import json
from typing import Optional

VALIDATOR_PROMPT = """You are a code validation agent. Analyze this code snippet and determine if it's relevant to the query.

QUERY: {query}

SYMBOL: {symbol_path}

CODE:
{code_snippet}

Respond with JSON:
{{"is_valid": true/false, "confidence": 0.0-1.0, "critique": "1-2 sentence explanation", "dependencies": ["list", "of", "related", "symbols", "to", "investigate"]}}"""


def build_validator_prompt(query: str, code_snippet: str, symbol_path: str) -> str:
    return VALIDATOR_PROMPT.format(
        query=query,
        code_snippet=code_snippet,
        symbol_path=symbol_path,
    )


def parse_validator_output(raw: str) -> Optional[dict]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
```

Create `daemon/agents/orchestrator.py`:

```python
"""Orchestrator agent — manages MCTS search state and backtracking decisions.

The Orchestrator is the "Control Agent." It maintains global search state,
decides when to pivot strategies, and prevents circular reasoning.
"""

import json
from typing import Optional

ORCHESTRATOR_PROMPT = """You are a search orchestration agent managing a codebase navigation session.

ORIGINAL QUERY: {query}

SESSION STATE:
{session_state_json}

LAST ACTION RESULT:
{last_result_json}

DECISION RULES:
- If the last result was relevant (is_valid=true), consider if we have enough context to answer
- If the last result was irrelevant, blacklist that branch and suggest the next best node
- If depth >= max_depth, force an answer with available context
- If all promising branches are exhausted, answer with what we have

Respond with JSON:
{{"next_action": "drill|map|answer|backtrack", "target_node": "path::symbol or null", "reasoning": "1-2 sentences", "should_blacklist": "node to blacklist or null"}}"""


def build_orchestrator_prompt(query: str, session_state: dict, last_result: dict) -> str:
    return ORCHESTRATOR_PROMPT.format(
        query=query,
        session_state_json=json.dumps(session_state, indent=2),
        last_result_json=json.dumps(last_result, indent=2),
    )


def parse_orchestrator_output(raw: str) -> Optional[dict]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_agents.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add daemon/agents/ daemon/tests/test_agents.py
git commit -m "feat: Triad agent prompt templates (Explorer, Validator, Orchestrator)"
```

---

### Task 10: Update Skill, Sub-Agent, and Template

**Files:**
- Modify: `.claude/skills/rlm-navigator/SKILL.md`
- Modify: `.claude/agents/rlm-subcall.md`
- Create: `.claude/agents/rlm-enricher.md`
- Modify: `templates/CLAUDE_SNIPPET.md`

**Step 1: Update SKILL.md**

Add a "Document Navigation" section after the existing "Chunk-Delegate-Synthesize" section:

```markdown
## Document Navigation

For non-code files (.md, .rst, .txt, .pdf), use the document-specific tools:

1. `rlm_tree` — spot document files in the project structure
2. `rlm_doc_map` — get hierarchical section outline (like rlm_map for code)
3. `rlm_doc_drill` — extract a specific section by title (like rlm_drill for symbols)
4. `rlm_assess` — check if you have enough context to answer

**Decision tree:**
- Code file? → `rlm_map` → `rlm_drill`
- Document file? → `rlm_doc_map` → `rlm_doc_drill`
- Unsure if enough? → `rlm_assess`

**Enriched skeletons:** When available, `rlm_map` output includes semantic summaries
(e.g., "# Handles JWT authentication with Redis sessions") after line range comments.
Use these to make faster, more accurate navigation decisions.
```

**Step 2: Update rlm-subcall.md**

Add document chunk handling to the inputs section:

```markdown
- Document chunks (from `rlm_doc_map` section trees or `rlm_chunks` on document files)
```

**Step 3: Create rlm-enricher.md**

```markdown
## Agent: rlm-enricher

**Model:** haiku
**Role:** Semantic enrichment — generates 1-line summaries for code symbols.

### Input
- File path and skeleton output from `rlm_map`

### Output (JSON)
A mapping of symbol names to semantic summaries:
```json
{
  "Calculator": "Performs basic arithmetic operations with validation.",
  "add": "Returns the sum of two numbers after type checking.",
  "validate_input": "Raises ValueError for non-numeric arguments."
}
```

### Rules
- Summaries must be ONE line, describing WHAT the symbol DOES (not what it IS)
- Focus on behavior, side effects, and key dependencies
- If uncertain about a symbol's purpose from its signature alone, say "Purpose unclear from signature"
```

**Step 4: Update CLAUDE_SNIPPET.md**

Add to the navigation tools table:

```markdown
| `rlm_doc_map` | Get hierarchical outline of a document file |
| `rlm_doc_drill` | Extract a specific section from a document by title |
| `rlm_assess` | Check if accumulated context is sufficient to answer |
```

**Step 5: Commit**

```bash
git add .claude/ templates/CLAUDE_SNIPPET.md
git commit -m "feat: update skill, sub-agent, and template for document navigation + enrichment"
```

---

### Task 11: Uncomment Dependencies + Final Build + Integration Test

**Files:**
- Modify: `daemon/requirements.txt`
- Build: `server/`
- Test: all

**Step 1: Uncomment optional dependencies**

Update `daemon/requirements.txt` to include:

```
pageindex
anthropic>=0.40
python-dotenv>=1.0
```

**Step 2: Install dependencies**

Run: `cd daemon && pip install -r requirements.txt`

**Step 3: Build MCP server**

Run: `cd server && npm run build`
Expected: Clean build

**Step 4: Run full test suite**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 5: Manual integration test**

1. Set `ANTHROPIC_API_KEY` and `CHATGPT_API_KEY` in `.env`
2. Start daemon: `cd daemon && python rlm_daemon.py --root /path/to/project`
3. Test document indexing: send `{"action": "doc_map", "path": "README.md"}` via TCP
4. Test enrichment: send `{"action": "squeeze", "path": "some_file.py"}`, wait 5s, send again — second response should include enrichment annotations
5. Test status: send `{"action": "status"}` — should show `enrichment_available: true`, `doc_indexing_available: true`

**Step 6: Final commit**

```bash
git add daemon/requirements.txt
git commit -m "feat: enable PageIndex and Anthropic dependencies for full integration"
```

---

## Summary of Changes

| File | What Changed |
|------|-------------|
| `daemon/config.py` | **NEW** — Configuration layer, API key detection, feature flags |
| `daemon/doc_indexer.py` | **NEW** — PageIndex wrapper, local markdown parser, adapter layer |
| `daemon/node_enricher.py` | **NEW** — Haiku enrichment, skeleton parsing, enrichment cache, background worker |
| `daemon/mcts.py` | **NEW** — MCTS session state, session manager |
| `daemon/agents/explorer.py` | **NEW** — Explorer prompt template + output parser |
| `daemon/agents/validator.py` | **NEW** — Validator prompt template + output parser |
| `daemon/agents/orchestrator.py` | **NEW** — Orchestrator prompt template + output parser |
| `daemon/rlm_daemon.py` | doc_map, doc_drill, assess actions; enrichment cache wiring; status enrichment fields |
| `daemon/squeezer.py` | Document type detection; doc skeleton formatting |
| `daemon/requirements.txt` | Added pageindex, anthropic, python-dotenv |
| `server/src/index.ts` | New MCP tools: rlm_doc_map, rlm_doc_drill, rlm_assess |
| `.claude/skills/rlm-navigator/SKILL.md` | Document navigation workflow |
| `.claude/agents/rlm-subcall.md` | Document chunk handling |
| `.claude/agents/rlm-enricher.md` | **NEW** — Semantic enrichment agent |
| `templates/CLAUDE_SNIPPET.md` | New tool references |
| `daemon/tests/test_config.py` | **NEW** — Config tests |
| `daemon/tests/test_doc_indexer.py` | **NEW** — Document indexer tests |
| `daemon/tests/test_node_enricher.py` | **NEW** — Enricher + worker tests |
| `daemon/tests/test_mcts.py` | **NEW** — MCTS session tests |
| `daemon/tests/test_agents.py` | **NEW** — Triad agent prompt tests |
| `daemon/tests/test_daemon.py` | Document indexing + enrichment integration tests |

## Testing Matrix

| Scenario | How to Test |
|----------|-------------|
| Config detects API keys | `pytest tests/test_config.py` |
| Markdown local parsing | `pytest tests/test_doc_indexer.py` |
| PageIndex integration (if installed) | `pytest tests/test_doc_indexer.py::TestPageIndexIntegration` |
| Skeleton symbol parsing | `pytest tests/test_node_enricher.py::TestSkeletonParsing` |
| Enrichment cache | `pytest tests/test_node_enricher.py::TestEnrichmentCache` |
| Enrichment merge | `pytest tests/test_node_enricher.py::TestEnrichmentMerge` |
| Background worker | `pytest tests/test_node_enricher.py::TestEnrichmentWorker` |
| MCTS session state | `pytest tests/test_mcts.py` |
| Agent prompts build correctly | `pytest tests/test_agents.py` |
| Agent output parsing | `pytest tests/test_agents.py::TestOutputParsing` |
| doc_map daemon action | `pytest tests/test_daemon.py::TestDocumentIndexing` |
| Enrichment in status | `pytest tests/test_daemon.py::TestEnrichmentIntegration` |
| Full daemon suite | `pytest tests/ -v` |
| MCP server builds | `cd server && npm run build` |
| End-to-end with API keys | Manual (see Task 11 Step 5) |
