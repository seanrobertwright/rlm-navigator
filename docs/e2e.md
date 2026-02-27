# RLM Navigator — Comprehensive E2E Testing Strategy

## Overview

RLM Navigator has 159 unit tests covering individual components, but lacks integration and end-to-end test coverage. This document defines a comprehensive E2E testing strategy covering all integration points between the daemon, MCP server, file watcher, REPL, and external APIs.

### Testing Pyramid Context

| Layer | Current | Target |
|-------|---------|--------|
| Unit tests | 159 (strong) | Maintain |
| Integration tests | ~5 (TCP only) | +30 |
| E2E tests | 0 | +15 |

### Coverage Gaps by Component

| Component | Unit | Integration | E2E | Priority |
|-----------|------|-------------|-----|----------|
| Daemon TCP + Lock file | ✓ | ✓ | ✗ | HIGH |
| File Watcher + Cache invalidation | ✗ | ✗ | ✗ | HIGH |
| ChunkStore + Manifest | ✗ | ✗ | ✗ | HIGH |
| MCP Server (TypeScript) | ✗ | ✗ | ✗ | CRITICAL |
| Daemon ↔ MCP Integration | ✗ | ✗ | ✗ | CRITICAL |
| Doc Indexing (Markdown + API) | ✓ (local) | ✗ | ✗ | HIGH |
| Node Enrichment (Haiku + Cache) | ✓ (mock) | ✗ | ✗ | HIGH |
| MCTS Navigation Flow | ✓ (prompts) | ✗ | ✗ | MEDIUM |
| REPL + Persistence | ✓ | ✗ | ✗ | MEDIUM |

---

## Architecture — Integration Points

```
┌─────────────────────────────────────────────────────┐
│                    Claude Code                       │
│                  (MCP Client)                        │
└──────────────────────┬──────────────────────────────┘
                       │ MCP protocol (stdio)
┌──────────────────────▼──────────────────────────────┐
│              MCP Server (index.ts)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ Tool defs   │  │ Port discovery│  │ Auto-spawn │  │
│  │ (13 tools)  │  │ .rlm/port    │  │ + retry    │  │
│  └──────┬──────┘  └──────┬───────┘  └─────┬──────┘  │
└─────────┼────────────────┼─────────────────┼─────────┘
          │                │                 │
          │    TCP JSON    │   spawn python  │
          │    protocol    │   rlm_daemon.py │
┌─────────▼────────────────▼─────────────────▼─────────┐
│              Python Daemon (rlm_daemon.py)             │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌────────┐  │
│  │ Skeleton │ │ Chunk    │ │ File      │ │ REPL   │  │
│  │ Cache    │ │ Store    │ │ Watcher   │ │        │  │
│  └────┬─────┘ └────┬─────┘ └─────┬─────┘ └───┬────┘  │
│       │             │             │            │       │
│  ┌────▼─────┐ ┌────▼─────┐ ┌─────▼─────┐     │       │
│  │ Squeezer │ │ .rlm/    │ │ Watchdog  │     │       │
│  │ (AST)    │ │ chunks/  │ │ Observer  │     │       │
│  └──────────┘ └──────────┘ └───────────┘     │       │
│                                               │       │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐   │       │
│  │ Doc      │ │ Node     │ │ MCTS +      │   │       │
│  │ Indexer  │ │ Enricher │ │ Agents      │   │       │
│  └────┬─────┘ └────┬─────┘ └─────────────┘   │       │
│       │             │                          │       │
└───────┼─────────────┼──────────────────────────┼───────┘
        │             │                          │
   ┌────▼────┐   ┌────▼──────┐            ┌─────▼─────┐
   │PageIndex│   │ Anthropic │            │ Pickle    │
   │ API     │   │ Haiku API │            │ State     │
   └─────────┘   └───────────┘            └───────────┘
```

---

## E2E Test Flows

### Flow 1: Daemon Lifecycle

**What it tests:** Daemon startup, port binding, lock files, idle timeout, shutdown, cleanup.

**Steps:**
1. Create a `tmp_path` project with `.rlm/` directory and a few source files
2. Start daemon via `run_server(root, port, idle_timeout=5)`
3. Verify `.rlm/port` file is written with correct JSON `{port, pid}`
4. Verify `.rlm/daemon.lock` file is written with correct PID
5. Send `{"action": "status"}` via TCP — verify `status: "alive"` and correct root
6. Attempt second `run_server()` on same root — verify `SystemExit` raised (lock blocks)
7. Send `{"action": "shutdown"}` via TCP — verify daemon exits cleanly
8. Verify `.rlm/port` and `.rlm/daemon.lock` are removed
9. Verify `.rlm/sessions.jsonl` contains session stats entry

**Idle timeout variant:**
1. Start daemon with `idle_timeout=3`
2. Wait 5 seconds (no requests)
3. Verify daemon shuts down automatically
4. Verify cleanup files removed

**Stale lock recovery variant:**
1. Write a lock file with a dead PID (e.g., 999999999)
2. Call `check_lock_file()` — verify it returns None and deletes lock
3. Start daemon — verify it starts successfully

```python
# Test location: daemon/tests/test_e2e_lifecycle.py

class TestDaemonLifecycleE2E:
    def test_full_lifecycle(self, tmp_path):
        """Start → status → shutdown → cleanup."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (tmp_path / "main.py").write_text("def hello(): pass\n")

        port = 19200
        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=0)
            shutdown_complete.set()

        t = threading.Thread(target=run_and_signal, daemon=True)
        t.start()
        time.sleep(1)

        # Verify port + lock files
        assert (rlm_dir / "port").exists()
        assert (rlm_dir / "daemon.lock").exists()

        port_data = json.loads((rlm_dir / "port").read_text())
        assert port_data["port"] == port

        # Status check
        resp = tcp_query(port, {"action": "status"})
        assert resp["status"] == "alive"

        # Shutdown
        resp = tcp_query(port, {"action": "shutdown"})
        assert resp["status"] == "shutting_down"
        assert shutdown_complete.wait(timeout=5)

        # Cleanup
        assert not (rlm_dir / "port").exists()
        assert not (rlm_dir / "daemon.lock").exists()

    def test_idle_timeout_shutdown(self, tmp_path):
        """Daemon should auto-shutdown after idle timeout."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (tmp_path / "test.py").write_text("x = 1\n")

        shutdown_complete = threading.Event()
        def run_and_signal():
            run_server(str(tmp_path), 19201, idle_timeout=2)
            shutdown_complete.set()

        t = threading.Thread(target=run_and_signal, daemon=True)
        t.start()
        assert shutdown_complete.wait(timeout=10)
```

---

### Flow 2: File Watcher Integration

**What it tests:** File changes trigger cache invalidation, rechunking, and REPL staleness.

**Steps:**
1. Start daemon with file watcher active
2. Create a Python file and squeeze it (populates cache)
3. Modify the file content
4. Wait for watchdog event propagation (~1s)
5. Squeeze again — verify new skeleton reflects changes
6. Delete the file — verify cache returns None
7. Verify ChunkStore manifest is updated after modification

**REPL staleness variant:**
1. Start daemon + init REPL
2. Execute `peek("test.py", 1, 5)` in REPL
3. Modify `test.py` externally
4. Check REPL status — verify staleness warning for `test.py`

```python
class TestFileWatcherE2E:
    def test_modify_invalidates_cache(self, tmp_path):
        """Modifying a file should invalidate its skeleton cache."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        f = tmp_path / "app.py"
        f.write_text("def original(): pass\n")

        port = 19202
        t = threading.Thread(
            target=run_server,
            args=(str(tmp_path), port, 0),
            daemon=True,
        )
        t.start()
        time.sleep(1)

        # Initial squeeze
        resp = tcp_query(port, {"action": "squeeze", "path": "app.py"})
        assert "original" in resp["skeleton"]

        # Modify file
        f.write_text("def modified(): pass\n")
        time.sleep(2)  # Wait for watchdog

        # Re-squeeze — should reflect change
        resp = tcp_query(port, {"action": "squeeze", "path": "app.py"})
        assert "modified" in resp["skeleton"]

        # Shutdown
        tcp_query(port, {"action": "shutdown"})
```

---

### Flow 3: MCP ↔ Daemon Communication

**What it tests:** Port discovery, daemon auto-spawn, retry logic, root validation, response truncation.

**Steps:**
1. Set up `.rlm/` directory with no port file (daemon not running)
2. MCP server calls `getDaemonPort()` — returns null
3. MCP server calls `spawnDaemon()` — spawns Python process
4. `waitForDaemon()` polls for `.rlm/port` file
5. Port file appears — MCP reads port
6. `validateDaemonRoot()` sends status check, verifies root match
7. Query flows through: MCP → TCP → daemon → response → MCP

**Root mismatch variant:**
1. Start daemon for project A
2. MCP server configured for project B reads stale port file
3. `validateDaemonRoot()` detects mismatch
4. Cleans up stale port file
5. Spawns new daemon for project B

**Note:** MCP server tests require a TypeScript test framework (vitest/jest). These tests should live in `server/tests/`.

```typescript
// Test location: server/tests/e2e.test.ts

describe("MCP ↔ Daemon E2E", () => {
  test("auto-spawn daemon on first query", async () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-e2e-"));
    const rlmDir = path.join(tmpDir, ".rlm");
    fs.mkdirSync(rlmDir);
    fs.writeFileSync(path.join(tmpDir, "test.py"), "def foo(): pass\n");

    // Copy daemon files to .rlm/daemon/
    // ...setup...

    // No port file → getDaemonPort() returns null
    expect(getDaemonPort()).toBeNull();

    // spawnDaemon() → waitForDaemon()
    spawnDaemon();
    const ok = await waitForDaemon(15000);
    expect(ok).toBe(true);

    // Port file should now exist
    expect(getDaemonPort()).not.toBeNull();

    // Query should work
    const status = await queryDaemonWithRetry({ action: "status" });
    expect(status.status).toBe("alive");
  });
});
```

---

### Flow 4: Code Navigation (Full Cycle)

**What it tests:** The complete code exploration workflow: tree → map → drill → search.

**Steps:**
1. Create a project with nested directories and multiple Python/JS files
2. `tree` action — verify directory structure with correct file types and sizes
3. `squeeze` (map) on a Python file — verify skeleton has classes/functions with line ranges
4. `find` (drill) on a symbol — verify correct line range returned
5. Read those lines — verify actual implementation code
6. `search` for a symbol name — verify it appears in search results across files
7. Verify session stats accumulate correctly (tokens served/avoided)

```python
class TestCodeNavigationE2E:
    def test_tree_map_drill_search_cycle(self, tmp_path):
        """Full navigation: tree → map → drill → search."""
        # Setup project
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

        # Step 1: Tree
        resp = json.loads(handle_request(
            json.dumps({"action": "tree"}).encode(), cache, root))
        names = [e["name"] for e in resp["tree"]]
        assert "src" in names

        # Step 2: Map
        resp = json.loads(handle_request(
            json.dumps({"action": "squeeze", "path": "src/auth.py"}).encode(),
            cache, root))
        assert "AuthManager" in resp["skeleton"]
        assert "validate_token" in resp["skeleton"]

        # Step 3: Drill
        resp = json.loads(handle_request(
            json.dumps({"action": "find", "path": "src/auth.py",
                         "symbol": "validate_token"}).encode(),
            cache, root))
        assert "start_line" in resp
        assert "end_line" in resp

        # Step 4: Search
        resp = json.loads(handle_request(
            json.dumps({"action": "search", "query": "validate_token"}).encode(),
            cache, root))
        assert len(resp["results"]) >= 1
        assert any("auth.py" in r["path"] for r in resp["results"])
```

---

### Flow 5: Document Navigation (Full Cycle)

**What it tests:** doc_map → doc_drill → assess workflow for markdown files.

**Steps:**
1. Create a project with README.md containing nested headings
2. `squeeze` on README.md — verify document skeleton with section names
3. `doc_map` — verify hierarchical tree with section titles and line ranges
4. `doc_drill` for a specific section — verify correct content extracted
5. `assess` — verify assessment string references query and context
6. `search` — verify markdown headings appear in search results

```python
class TestDocumentNavigationE2E:
    def test_doc_map_drill_assess_cycle(self, tmp_path):
        """Full document navigation: map → drill → assess."""
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
        resp = json.loads(handle_request(
            json.dumps({"action": "squeeze", "path": "README.md"}).encode(),
            cache, root))
        assert "document" in resp["skeleton"]
        assert "Installation" in resp["skeleton"]

        # doc_map returns structured tree
        resp = json.loads(handle_request(
            json.dumps({"action": "doc_map", "path": "README.md"}).encode(),
            cache, root))
        tree = resp["tree"]
        assert tree["type"] == "document"
        section_names = [c["name"] for c in tree["children"]]
        assert "Project Overview" in section_names

        # doc_drill extracts specific section
        resp = json.loads(handle_request(
            json.dumps({"action": "doc_drill", "path": "README.md",
                         "section": "Installation"}).encode(),
            cache, root))
        assert "pip install" in resp["content"]

        # assess provides guidance
        resp = json.loads(handle_request(
            json.dumps({"action": "assess",
                         "query": "How do I install?",
                         "context_summary": "Found Installation section"}).encode(),
            cache, root))
        assert "install" in resp["assessment"].lower()
```

---

### Flow 6: Node Enrichment (Haiku API)

**What it tests:** Skeleton enrichment pipeline: squeeze → parse → API call → cache → merge.

**Steps:**
1. Create a Python file with several classes/functions
2. Squeeze to get skeleton
3. Parse skeleton symbols — verify correct extraction
4. Mock Anthropic API to return enrichment JSON
5. Call `EnrichmentWorker.process_one()` — verify cache is populated
6. Merge enrichments into skeleton — verify annotations appear
7. Modify file (new mtime) — verify cache miss, re-enrichment triggered

**Graceful degradation variant:**
1. Mock Anthropic API to raise exception
2. `process_one()` should not crash
3. Cache should remain empty (no partial data)
4. Skeleton served without enrichments (fallback)

```python
class TestEnrichmentE2E:
    def test_enrichment_pipeline(self, tmp_path, monkeypatch):
        """Full enrichment: squeeze → parse → mock API → cache → merge."""
        f = tmp_path / "calc.py"
        f.write_text(
            "class Calculator:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
            "    def multiply(self, a, b):\n"
            "        return a * b\n"
        )

        from squeezer import squeeze
        from node_enricher import (
            parse_skeleton_symbols, EnrichmentCache,
            EnrichmentWorker, merge_enrichments,
        )

        skeleton = squeeze(str(f))
        symbols = parse_skeleton_symbols(skeleton)
        assert len(symbols) >= 2

        # Mock Anthropic API
        mock_response = {
            "Calculator": "Performs basic arithmetic operations.",
            "add": "Returns the sum of two numbers.",
            "multiply": "Returns the product of two numbers.",
        }

        class MockConfig:
            enrichment_enabled = True
            anthropic_api_key = "sk-test"

        class MockMessage:
            class content_item:
                text = json.dumps(mock_response)
            content = [content_item()]

        class MockMessages:
            def create(self, **kwargs):
                return MockMessage()

        class MockClient:
            messages = MockMessages()

        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: MockClient())

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=MockConfig())
        mtime = f.stat().st_mtime

        worker.enqueue(str(f), skeleton, mtime)
        worker.process_one()

        # Cache should be populated
        enrichments = cache.get(str(f), mtime)
        assert enrichments is not None
        assert "Calculator" in enrichments

        # Merge into skeleton
        enriched = merge_enrichments(skeleton, enrichments)
        assert "# Performs basic arithmetic operations." in enriched

    def test_api_failure_graceful_degradation(self, tmp_path, monkeypatch):
        """API failure should not crash; skeleton served without enrichment."""
        from node_enricher import EnrichmentCache, EnrichmentWorker

        class MockConfig:
            enrichment_enabled = True
            anthropic_api_key = "sk-test"

        monkeypatch.setattr("anthropic.Anthropic",
                            lambda **kw: (_ for _ in ()).throw(Exception("API down")))

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=MockConfig())
        worker.enqueue("test.py", "class Foo:  # L1-10\n", 1000.0)
        worker.process_one()  # Should not raise

        assert cache.get("test.py", 1000.0) is None
```

---

### Flow 7: ChunkStore Lifecycle

**What it tests:** File chunking, manifest management, mtime-based invalidation, atomic updates.

**Steps:**
1. Create a large file (500+ lines)
2. Initialize ChunkStore
3. `chunk_file()` — verify manifest and chunk files created
4. Read manifest — verify `total_chunks`, `chunk_size`, `overlap`, `total_lines`, `mtime`
5. Read each chunk — verify content matches source lines
6. Verify overlap: last N lines of chunk K = first N lines of chunk K+1
7. Modify the file — `chunk_file()` again — verify manifest updated
8. Delete the file — `remove_file()` — verify chunk directory removed
9. Scan entire project — verify all text files chunked, binaries skipped

```python
class TestChunkStoreE2E:
    def test_chunk_lifecycle(self, tmp_path):
        """Chunk → read → modify → re-chunk → delete."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        # Create a 500-line file
        f = tmp_path / "large.py"
        lines = [f"def func_{i}(): pass  # line {i}\n" for i in range(500)]
        f.write_text("".join(lines))

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.chunk_file(str(f))

        # Verify manifest
        manifest = store.get_manifest(str(f))
        assert manifest is not None
        assert manifest["total_lines"] == 500
        assert manifest["total_chunks"] >= 3

        # Read chunks and verify overlap
        chunk_0 = store.read_chunk(str(f), 0)
        chunk_1 = store.read_chunk(str(f), 1)
        assert chunk_0 is not None
        assert chunk_1 is not None

        # Verify content starts with header
        assert "large.py lines" in chunk_0.split("\n")[0]

        # Modify file → re-chunk
        f.write_text("".join(lines[:100]))  # Truncate
        store.chunk_file(str(f))
        manifest = store.get_manifest(str(f))
        assert manifest["total_lines"] == 100

        # Delete
        store.remove_file(str(f))
        assert store.get_manifest(str(f)) is None

    def test_binary_file_skipped(self, tmp_path):
        """Binary files should not be chunked."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.chunk_file(str(f))
        assert store.get_manifest(str(f)) is None

    def test_scan_all_chunks_project(self, tmp_path):
        """scan_all should chunk all text files, skip binaries."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        (tmp_path / "a.py").write_text("x = 1\n" * 50)
        (tmp_path / "b.js").write_text("var x = 1;\n" * 50)
        (tmp_path / "c.bin").write_bytes(b"\x00" * 100)

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.scan_all()

        assert store.get_manifest(str(tmp_path / "a.py")) is not None
        assert store.get_manifest(str(tmp_path / "b.js")) is not None
        assert store.get_manifest(str(tmp_path / "c.bin")) is None
```

---

### Flow 8: MCTS Navigation Session

**What it tests:** The Explorer → Validator → Orchestrator decision loop using session state.

**Steps:**
1. Create a session with a query
2. Build explorer prompt with tree skeleton + empty session state
3. Parse explorer output — verify selected nodes extracted
4. Update session: visit selected node, set score
5. Build validator prompt with drilled code
6. Parse validator output — verify is_valid and confidence
7. Build orchestrator prompt with session state + validation result
8. Parse orchestrator output — verify next_action decision
9. Verify session state mutations (visited, scores, blacklist)
10. Repeat until max_depth — verify `at_max_depth` forces answer

```python
class TestMCTSNavigationE2E:
    def test_full_navigation_loop(self):
        """Simulate Explorer → Validator → Orchestrator decision loop."""
        from mcts import MCTSSession
        from agents.explorer import build_explorer_prompt, parse_explorer_output
        from agents.validator import build_validator_prompt, parse_validator_output
        from agents.orchestrator import build_orchestrator_prompt, parse_orchestrator_output

        session = MCTSSession("How does authentication work?", max_depth=3)

        # Step 1: Explorer selects nodes
        explorer_prompt = build_explorer_prompt(
            query=session.query,
            tree_skeleton="class AuthManager:  # L1-50\nclass Database:  # L52-100\n",
            session_state=session.to_dict(),
        )
        assert "authentication" in explorer_prompt.lower()

        # Simulate explorer response
        explorer_result = parse_explorer_output(json.dumps({
            "selected_nodes": [
                {"path": "auth.py", "symbol": "AuthManager", "score": 0.95,
                 "reason": "Handles authentication"},
            ],
            "action": "drill",
        }))
        assert explorer_result["action"] == "drill"

        # Step 2: Visit node + validate
        session.visit("auth.py::AuthManager")
        session.set_score("auth.py", 0.95)

        validator_prompt = build_validator_prompt(
            query=session.query,
            code_snippet="class AuthManager:\n    def validate(self): ...",
            symbol_path="auth.py::AuthManager",
        )
        validator_result = parse_validator_output(json.dumps({
            "is_valid": True, "confidence": 0.9,
            "critique": "Directly handles auth logic.",
            "dependencies": ["token.py::TokenStore"],
        }))
        assert validator_result["is_valid"]

        # Step 3: Orchestrator decides next action
        orchestrator_prompt = build_orchestrator_prompt(
            query=session.query,
            session_state=session.to_dict(),
            last_result={"action": "drill", "verdict": "relevant"},
        )
        orchestrator_result = parse_orchestrator_output(json.dumps({
            "next_action": "drill",
            "target_node": "token.py::TokenStore",
            "reasoning": "Need to understand token storage.",
            "should_blacklist": None,
        }))
        assert orchestrator_result["next_action"] == "drill"

        # Step 4: Continue until max depth
        session.visit("token.py::TokenStore")
        session.visit("session.py::SessionManager")
        assert session.at_max_depth
        assert session.depth == 3

    def test_blacklisting_flow(self):
        """Irrelevant nodes should be blacklisted and skipped."""
        from mcts import MCTSSession

        session = MCTSSession("auth query", max_depth=5)
        session.visit("utils.py::format_date")
        session.blacklist_node("utils.py::format_date")

        state = session.to_dict()
        assert "utils.py::format_date" in state["blacklist"]
        assert "utils.py::format_date" in state["visited"]
```

---

## Mocking Strategies

### Anthropic API (Haiku Enrichment)

```python
@pytest.fixture
def mock_anthropic(monkeypatch):
    """Mock Anthropic SDK for enrichment tests."""
    enrichments = {"Calculator": "Performs arithmetic.", "add": "Sums two numbers."}

    class MockContent:
        text = json.dumps(enrichments)

    class MockResponse:
        content = [MockContent()]

    class MockMessages:
        def create(self, **kwargs):
            return MockResponse()

    class MockClient:
        messages = MockMessages()

    monkeypatch.setattr("anthropic.Anthropic", lambda **kw: MockClient())
    return enrichments
```

### PageIndex API (Document Indexing)

```python
@pytest.fixture
def mock_pageindex(monkeypatch):
    """Mock PageIndex for document indexing tests."""
    async def mock_md_to_tree(**kwargs):
        return {
            "nodes": [
                {"title": "Introduction", "start_index": 1, "end_index": 5,
                 "summary": "Overview of the project.", "nodes": []},
            ]
        }

    monkeypatch.setattr("pageindex.page_index_md.md_to_tree", mock_md_to_tree)
```

### TCP Communication Helper

```python
def tcp_query(port: int, request: dict, timeout: float = 5.0) -> dict:
    """Send a JSON request to the daemon and return parsed response."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", port))
    s.send(json.dumps(request).encode())
    data = b""
    while True:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        except socket.timeout:
            break
    s.close()
    return json.loads(data.decode("utf-8"))
```

### Port Allocation Helper

```python
import random

def get_free_port() -> int:
    """Get a random free port in the test range."""
    base = 19200 + random.randint(0, 500)
    for port in range(base, base + 20):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError("No free port found")
```

---

## Test Infrastructure

### File Layout

```
daemon/tests/
├── conftest.py                # Shared fixtures (tcp_query, get_free_port, mock_*)
├── test_squeezer.py           # Unit: AST parsing
├── test_daemon.py             # Unit + Integration: handle_request, TCP server
├── test_repl.py               # Unit: REPL persistence, helpers
├── test_config.py             # Unit: configuration
├── test_doc_indexer.py        # Unit: markdown parsing
├── test_node_enricher.py      # Unit: enrichment parsing, cache
├── test_mcts.py               # Unit: session state
├── test_agents.py             # Unit: prompt templates
├── test_e2e_lifecycle.py      # E2E: daemon lifecycle
├── test_e2e_navigation.py     # E2E: code + doc navigation flows
├── test_e2e_enrichment.py     # E2E: enrichment pipeline with mocked API
├── test_e2e_chunks.py         # E2E: ChunkStore lifecycle
├── test_e2e_watcher.py        # E2E: file watcher integration
└── test_e2e_mcts.py           # E2E: MCTS navigation loop

server/tests/
├── setup.ts                   # Test helpers (spawn daemon, port mgmt)
└── e2e.test.ts                # E2E: MCP ↔ daemon integration
```

### Pytest Markers

```python
# conftest.py
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests (may be slow)")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "requires_api: tests requiring real API keys")
```

**Usage:**
```bash
# Unit tests only (fast)
pytest tests/ -m "not e2e and not integration"

# Integration + E2E (slower)
pytest tests/ -m "e2e or integration"

# Everything except real API tests
pytest tests/ -m "not requires_api"

# Full suite
pytest tests/ -v
```

### Timeouts

E2E tests involving daemon startup should use generous timeouts:

```python
@pytest.fixture
def daemon_server(tmp_path):
    """Start a daemon server and yield (port, root). Cleanup on teardown."""
    port = get_free_port()
    rlm_dir = tmp_path / ".rlm"
    rlm_dir.mkdir()

    shutdown = threading.Event()
    def run():
        run_server(str(tmp_path), port, idle_timeout=0)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1)  # Wait for startup

    yield port, str(tmp_path)

    # Teardown: send shutdown
    try:
        tcp_query(port, {"action": "shutdown"}, timeout=2)
    except Exception:
        pass
```

---

## Implementation Priority

| Priority | Test Suite | Tests | Effort | Risk Covered |
|----------|-----------|-------|--------|-------------|
| 1 | `test_e2e_lifecycle.py` | 4 | Low | Daemon start/stop, lock files, idle timeout |
| 2 | `test_e2e_navigation.py` | 3 | Low | Code + doc navigation full cycle |
| 3 | `test_e2e_chunks.py` | 3 | Low | ChunkStore lifecycle, binary skip |
| 4 | `test_e2e_enrichment.py` | 2 | Medium | API mock, graceful degradation |
| 5 | `test_e2e_watcher.py` | 2 | Medium | File changes → cache invalidation |
| 6 | `test_e2e_mcts.py` | 2 | Low | Navigation loop, blacklisting |
| 7 | `server/tests/e2e.test.ts` | 3 | High | MCP ↔ daemon, auto-spawn, root validation |

**Total: ~19 E2E tests across 7 test files.**

---

## CI/CD Considerations

### GitHub Actions

```yaml
- name: Run unit tests
  run: cd daemon && python -m pytest tests/ -m "not e2e" -v

- name: Run E2E tests
  run: cd daemon && python -m pytest tests/ -m "e2e" -v --timeout=30

- name: Build MCP server
  run: cd server && npm run build

- name: Run MCP E2E tests
  run: cd server && npm test
```

### Parallel Safety

- Each E2E test uses `tmp_path` for isolation (no shared filesystem state)
- Port allocation uses `get_free_port()` to avoid conflicts
- Daemon processes are started as daemon threads (auto-cleanup)
- Lock files are scoped to `tmp_path` (no cross-test interference)

### Flakiness Prevention

- Use `threading.Event` for synchronization (not `time.sleep` alone)
- Generous timeouts for daemon startup (1-2s sleep + event wait)
- Retry TCP connections on ECONNREFUSED (daemon may still be binding)
- Skip TCP tests if port is genuinely in use (existing pattern in test_daemon.py)
