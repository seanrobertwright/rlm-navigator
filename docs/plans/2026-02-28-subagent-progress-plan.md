# Sub-Agent Progress Visibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give users real-time visual feedback when Haiku sub-agents perform work, via a new `rlm_progress` MCP tool, daemon session tracking, and skill instruction mandates.

**Architecture:** New `progress` action in daemon stores events + counters in SessionStats. New `rlm_progress` MCP tool formats human-readable messages and forwards to daemon. SKILL.md mandates calling rlm_progress at each workflow phase boundary.

**Tech Stack:** Python (daemon), TypeScript (MCP server), Markdown (skill instructions)

---

### Task 1: Add progress tracking to SessionStats

**Files:**
- Modify: `daemon/rlm_daemon.py:113-155` (SessionStats class)
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_daemon.py` at the end of the file, new test class:

```python
class TestSessionProgress:
    def test_progress_summary_initial(self):
        """SessionStats starts with empty progress data."""
        stats = SessionStats()
        d = stats.to_dict()
        assert d["progress_summary"]["sub_agent_dispatches"] == 0
        assert d["progress_summary"]["chunks_analyzed"] == 0
        assert d["progress_summary"]["answers_found"] == 0
        assert d["progress_summary"]["enrichments"] == 0
        assert d["progress_summary"]["analyses"] == 0
        assert d["progress_events"] == []

    def test_record_progress_chunk_dispatch(self):
        """record_progress increments dispatch counters."""
        stats = SessionStats()
        stats.record_progress("chunk_dispatch", {"agent": "rlm-subcall", "file": "main.py", "chunk": 0, "total_chunks": 3})
        d = stats.to_dict()
        assert d["progress_summary"]["sub_agent_dispatches"] == 1
        assert d["progress_summary"]["analyses"] == 1
        assert d["progress_summary"]["enrichments"] == 0
        assert len(d["progress_events"]) == 1
        assert d["progress_events"][0]["event"] == "chunk_dispatch"

    def test_record_progress_enrichment_dispatch(self):
        """record_progress counts enrichment dispatches separately."""
        stats = SessionStats()
        stats.record_progress("chunk_dispatch", {"agent": "rlm-enricher", "file": "main.py"})
        d = stats.to_dict()
        assert d["progress_summary"]["enrichments"] == 1
        assert d["progress_summary"]["analyses"] == 0

    def test_record_progress_chunk_complete(self):
        """chunk_complete increments chunks_analyzed."""
        stats = SessionStats()
        stats.record_progress("chunk_complete", {"chunk": 0, "total_chunks": 3})
        d = stats.to_dict()
        assert d["progress_summary"]["chunks_analyzed"] == 1

    def test_record_progress_answer_found(self):
        """answer_found increments counter."""
        stats = SessionStats()
        stats.record_progress("answer_found", {"summary": "Found the auth handler"})
        d = stats.to_dict()
        assert d["progress_summary"]["answers_found"] == 1

    def test_progress_events_have_timestamps(self):
        """Each event gets a timestamp."""
        stats = SessionStats()
        stats.record_progress("chunking_start", {"file": "main.py"})
        d = stats.to_dict()
        assert "timestamp" in d["progress_events"][0]

    def test_last_event_tracked(self):
        """to_dict includes last_event for display."""
        stats = SessionStats()
        stats.record_progress("chunk_dispatch", {"file": "main.py", "chunk": 2, "total_chunks": 5, "agent": "rlm-subcall"})
        stats.record_progress("chunk_complete", {"file": "main.py", "chunk": 2, "total_chunks": 5})
        d = stats.to_dict()
        assert d["progress_last_event"]["event"] == "chunk_complete"
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestSessionProgress -v`
Expected: FAIL — `SessionStats` has no `record_progress` method, `to_dict` has no `progress_summary`

**Step 3: Write minimal implementation**

In `daemon/rlm_daemon.py`, modify the `SessionStats` class:

```python
class SessionStats:
    """Tracks token savings across a daemon session."""

    def __init__(self):
        self._lock = threading.Lock()
        self.session_start = time.time()
        self.tool_calls = 0
        self.bytes_served = 0
        self.bytes_avoided = 0
        self.per_action: dict[str, dict] = {}
        self.progress_events: list[dict] = []
        self.progress_summary = {
            "sub_agent_dispatches": 0,
            "chunks_analyzed": 0,
            "answers_found": 0,
            "enrichments": 0,
            "analyses": 0,
        }

    def record(self, action: str, served_bytes: int, avoided_bytes: int = 0):
        with self._lock:
            self.tool_calls += 1
            self.bytes_served += served_bytes
            self.bytes_avoided += avoided_bytes
            if action not in self.per_action:
                self.per_action[action] = {"calls": 0, "bytes_served": 0, "bytes_avoided": 0}
            self.per_action[action]["calls"] += 1
            self.per_action[action]["bytes_served"] += served_bytes
            self.per_action[action]["bytes_avoided"] += avoided_bytes

    def record_progress(self, event: str, details: dict):
        with self._lock:
            entry = {"event": event, "details": details, "timestamp": time.time()}
            self.progress_events.append(entry)

            if event == "chunk_dispatch":
                self.progress_summary["sub_agent_dispatches"] += 1
                agent = details.get("agent", "")
                if agent == "rlm-enricher":
                    self.progress_summary["enrichments"] += 1
                else:
                    self.progress_summary["analyses"] += 1
            elif event == "chunk_complete":
                self.progress_summary["chunks_analyzed"] += 1
            elif event == "answer_found":
                self.progress_summary["answers_found"] += 1

    def to_dict(self) -> dict:
        with self._lock:
            total_bytes = self.bytes_served + self.bytes_avoided
            reduction_pct = round(self.bytes_avoided / total_bytes * 100) if total_bytes > 0 else 0
            breakdown = {}
            for action, data in self.per_action.items():
                entry: dict = {
                    "calls": data["calls"],
                    "tokens_served": data["bytes_served"] // 4,
                }
                if data["bytes_avoided"] > 0:
                    entry["tokens_avoided"] = data["bytes_avoided"] // 4
                breakdown[action] = entry
            result = {
                "tool_calls": self.tool_calls,
                "tokens_served": self.bytes_served // 4,
                "tokens_avoided": self.bytes_avoided // 4,
                "reduction_pct": reduction_pct,
                "duration_s": round(time.time() - self.session_start),
                "breakdown": breakdown,
                "progress_events": [dict(e) for e in self.progress_events],
                "progress_summary": dict(self.progress_summary),
            }
            if self.progress_events:
                result["progress_last_event"] = dict(self.progress_events[-1])
            return result
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestSessionProgress -v`
Expected: PASS (all 7 tests)

**Step 5: Run full daemon test suite for regression**

Run: `cd daemon && python -m pytest tests/test_daemon.py -v`
Expected: All existing tests PASS

**Step 6: Commit**

```bash
git add daemon/rlm_daemon.py daemon/tests/test_daemon.py
git commit -m "feat: add progress tracking to SessionStats"
```

---

### Task 2: Add `progress` action handler to daemon

**Files:**
- Modify: `daemon/rlm_daemon.py:781-788` (add before the `else` branch in `_handle_request_inner`)
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_daemon.py`, new test class:

```python
class TestProgressAction:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "test.py").write_text("def foo(): pass\n")
        return str(tmp_path)

    def test_progress_action_returns_ok(self, project):
        """Progress action stores event and returns ok."""
        cache = SkeletonCache()
        stats = SessionStats()
        data = json.dumps({
            "action": "progress",
            "event": "chunk_dispatch",
            "details": {"file": "main.py", "agent": "rlm-subcall", "chunk": 0, "total_chunks": 3}
        }).encode()
        resp = json.loads(handle_request(data, cache, project, stats=stats))
        assert resp["ok"] is True

    def test_progress_action_updates_stats(self, project):
        """Progress action increments session counters."""
        cache = SkeletonCache()
        stats = SessionStats()
        data = json.dumps({
            "action": "progress",
            "event": "chunk_dispatch",
            "details": {"file": "main.py", "agent": "rlm-subcall", "chunk": 0, "total_chunks": 3}
        }).encode()
        handle_request(data, cache, project, stats=stats)
        d = stats.to_dict()
        assert d["progress_summary"]["sub_agent_dispatches"] == 1

    def test_progress_action_without_stats(self, project):
        """Progress action returns ok even without stats object."""
        cache = SkeletonCache()
        data = json.dumps({
            "action": "progress",
            "event": "chunking_start",
            "details": {"file": "main.py"}
        }).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert resp["ok"] is True

    def test_progress_action_missing_event(self, project):
        """Progress action returns error when event is missing."""
        cache = SkeletonCache()
        data = json.dumps({
            "action": "progress",
            "details": {"file": "main.py"}
        }).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "error" in resp

    def test_progress_action_invalid_event(self, project):
        """Progress action rejects unknown event types."""
        cache = SkeletonCache()
        data = json.dumps({
            "action": "progress",
            "event": "invalid_event",
            "details": {}
        }).encode()
        resp = json.loads(handle_request(data, cache, project))
        assert "error" in resp
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestProgressAction -v`
Expected: FAIL — `Unknown action: progress`

**Step 3: Write minimal implementation**

In `daemon/rlm_daemon.py`, add this block before the `elif action == "shutdown":` line (around line 781):

```python
    elif action == "progress":
        event = req.get("event", "")
        details = req.get("details", {})
        valid_events = {
            "chunking_start", "chunking_complete", "chunk_dispatch",
            "chunk_complete", "synthesis_start", "synthesis_complete",
            "answer_found", "queries_suggested",
        }
        if not event:
            return json.dumps({"error": "Missing 'event' field"}).encode("utf-8")
        if event not in valid_events:
            return json.dumps({"error": f"Invalid event: {event}. Valid: {sorted(valid_events)}"}).encode("utf-8")
        if stats:
            stats.record_progress(event, details)
        return json.dumps({"ok": True}).encode("utf-8")
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestProgressAction -v`
Expected: PASS (all 5 tests)

**Step 5: Run full daemon test suite for regression**

Run: `cd daemon && python -m pytest tests/test_daemon.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add daemon/rlm_daemon.py daemon/tests/test_daemon.py
git commit -m "feat: add progress action handler to daemon"
```

---

### Task 3: Add `rlm_progress` MCP tool to server

**Files:**
- Modify: `server/src/index.ts` (add new tool after rlm_repl_export, before `// Main`)

**Step 1: Add the progress message formatter**

In `server/src/utils.ts`, add this export at the end of the file:

```typescript
export function formatProgressMessage(event: string, details: Record<string, any>): string {
  const file = details.file || "unknown";
  const agent = details.agent || "sub-agent";
  const chunk = details.chunk;
  const total = details.total_chunks;
  const count = details.count;
  const summary = details.summary;
  const query = details.query;

  const chunkLabel = chunk !== undefined && total !== undefined
    ? `chunk ${chunk + 1}/${total}` : "";

  switch (event) {
    case "chunking_start":
      return `[RLM] Chunking ${file}${query ? ` for: ${query}` : ""}...`;
    case "chunking_complete":
      return `[RLM] ${file} split into ${total} chunks`;
    case "chunk_dispatch":
      return `[RLM] Dispatching ${chunkLabel ? chunkLabel + " of " : ""}${file} to ${agent}...`;
    case "chunk_complete": {
      const parts = [`[RLM] ${chunkLabel ? chunkLabel + " " : ""}complete`];
      if (count !== undefined) parts.push(`${count} relevant symbols found`);
      if (summary) parts.push(summary);
      return parts.join(" — ");
    }
    case "queries_suggested":
      return `[RLM] ${count || 0} follow-up queries suggested`;
    case "answer_found":
      return `[RLM] Answer found${summary ? ": " + summary : ""}`;
    case "synthesis_start":
      return `[RLM] Synthesis starting — analyzing ${count || "?"} findings`;
    case "synthesis_complete":
      return `[RLM] Synthesis complete${summary ? ": " + summary : ""}`;
    default:
      return `[RLM] ${event}`;
  }
}
```

**Step 2: Add the MCP tool**

In `server/src/index.ts`, add this tool registration after the `rlm_repl_export` tool (before the `// Main` section comment around line 933):

```typescript
// --- rlm_progress ---
server.tool(
  "rlm_progress",
  "Report sub-agent progress. Call this at each phase boundary during chunk-delegate-synthesize and enrichment workflows to provide visual feedback.",
  {
    event: z
      .enum([
        "chunking_start", "chunking_complete", "chunk_dispatch",
        "chunk_complete", "synthesis_start", "synthesis_complete",
        "answer_found", "queries_suggested",
      ])
      .describe("The progress event type"),
    details: z
      .object({
        file: z.string().optional().describe("File being processed"),
        agent: z.string().optional().describe("Sub-agent name (rlm-subcall or rlm-enricher)"),
        chunk: z.number().int().optional().describe("Current chunk index (0-based)"),
        total_chunks: z.number().int().optional().describe("Total chunks in batch"),
        query: z.string().optional().describe("User query being investigated"),
        count: z.number().int().optional().describe("Number of items (symbols, queries, etc.)"),
        summary: z.string().optional().describe("Brief result summary"),
      })
      .default({})
      .describe("Event details"),
  },
  async ({ event, details }) => {
    const message = formatProgressMessage(event, details);

    // Fire-and-forget to daemon — don't fail if daemon is down
    try {
      await queryDaemonWithRetry({ action: "progress", event, details }, 3000, 1);
    } catch {
      // Progress tracking is best-effort
    }

    return {
      content: [{ type: "text" as const, text: message }],
    };
  }
);
```

**Step 3: Add the import**

In `server/src/index.ts`, update the import from `./utils.js` (line 19-26) to include `formatProgressMessage`:

```typescript
import {
  truncateResponse,
  formatSize,
  formatTree,
  formatStats,
  formatStalenessWarning,
  readLines,
  isPidAlive,
  formatProgressMessage,
} from "./utils.js";
```

**Step 4: Build the server**

Run: `cd server && npm run build`
Expected: Build succeeds with no errors

**Step 5: Commit**

```bash
git add server/src/index.ts server/src/utils.ts
git commit -m "feat: add rlm_progress MCP tool for sub-agent visibility"
```

---

### Task 4: Add unit tests for formatProgressMessage

**Files:**
- Modify: `server/tests/utils.test.ts`

**Step 1: Write the tests**

Add to `server/tests/utils.test.ts`:

```typescript
describe("formatProgressMessage", () => {
  test("chunking_start with query", () => {
    const msg = formatProgressMessage("chunking_start", { file: "main.py", query: "auth flow" });
    expect(msg).toBe("[RLM] Chunking main.py for: auth flow...");
  });

  test("chunking_start without query", () => {
    const msg = formatProgressMessage("chunking_start", { file: "main.py" });
    expect(msg).toBe("[RLM] Chunking main.py...");
  });

  test("chunking_complete", () => {
    const msg = formatProgressMessage("chunking_complete", { file: "main.py", total_chunks: 7 });
    expect(msg).toBe("[RLM] main.py split into 7 chunks");
  });

  test("chunk_dispatch with chunk info", () => {
    const msg = formatProgressMessage("chunk_dispatch", {
      file: "main.py", agent: "rlm-subcall", chunk: 2, total_chunks: 7
    });
    expect(msg).toBe("[RLM] Dispatching chunk 3/7 of main.py to rlm-subcall...");
  });

  test("chunk_dispatch for enrichment", () => {
    const msg = formatProgressMessage("chunk_dispatch", {
      file: "main.py", agent: "rlm-enricher"
    });
    expect(msg).toBe("[RLM] Dispatching main.py to rlm-enricher...");
  });

  test("chunk_complete with count", () => {
    const msg = formatProgressMessage("chunk_complete", {
      chunk: 2, total_chunks: 7, count: 3
    });
    expect(msg).toBe("[RLM] chunk 3/7 complete — 3 relevant symbols found");
  });

  test("chunk_complete with summary", () => {
    const msg = formatProgressMessage("chunk_complete", {
      chunk: 0, total_chunks: 1, count: 2, summary: "found auth handler"
    });
    expect(msg).toBe("[RLM] chunk 1/1 complete — 2 relevant symbols found — found auth handler");
  });

  test("queries_suggested", () => {
    const msg = formatProgressMessage("queries_suggested", { count: 5 });
    expect(msg).toBe("[RLM] 5 follow-up queries suggested");
  });

  test("answer_found with summary", () => {
    const msg = formatProgressMessage("answer_found", { summary: "JWT validation in middleware" });
    expect(msg).toBe("[RLM] Answer found: JWT validation in middleware");
  });

  test("answer_found without summary", () => {
    const msg = formatProgressMessage("answer_found", {});
    expect(msg).toBe("[RLM] Answer found");
  });

  test("synthesis_start", () => {
    const msg = formatProgressMessage("synthesis_start", { count: 12 });
    expect(msg).toBe("[RLM] Synthesis starting — analyzing 12 findings");
  });

  test("synthesis_complete", () => {
    const msg = formatProgressMessage("synthesis_complete", { summary: "3 key patterns identified" });
    expect(msg).toBe("[RLM] Synthesis complete: 3 key patterns identified");
  });

  test("unknown event fallback", () => {
    const msg = formatProgressMessage("custom_event", {});
    expect(msg).toBe("[RLM] custom_event");
  });
});
```

Add the import at the top of the test file:

```typescript
import { formatProgressMessage } from "../src/utils.js";
```

**Step 2: Run tests to verify they pass**

Run: `cd server && npx vitest run tests/utils.test.ts`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add server/tests/utils.test.ts
git commit -m "test: add formatProgressMessage unit tests"
```

---

### Task 5: Enhance `get_status` to show sub-agent activity

**Files:**
- Modify: `server/src/index.ts:303-360` (get_status tool handler)

**Step 1: Update the get_status handler**

In `server/src/index.ts`, within the `get_status` tool handler, after the session stats block (after the `for` loop that builds breakdown lines, around line 336), add:

```typescript
        if (status.session?.progress_summary) {
          const p = status.session.progress_summary;
          const total = p.sub_agent_dispatches;
          if (total > 0) {
            text += `\n\nSub-agent Activity:`;
            text += `\n  Dispatches: ${total} (${p.analyses} chunk analysis, ${p.enrichments} enrichment)`;
            text += `\n  Chunks analyzed: ${p.chunks_analyzed} | Answers found: ${p.answers_found}`;
            if (status.session.progress_last_event) {
              const last = status.session.progress_last_event;
              const lastMsg = formatProgressMessage(last.event, last.details || {});
              text += `\n  Last: ${lastMsg}`;
            }
          }
        }
```

**Step 2: Build the server**

Run: `cd server && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add server/src/index.ts
git commit -m "feat: show sub-agent activity in get_status output"
```

---

### Task 6: Update SKILL.md with progress call mandates

**Files:**
- Modify: `.claude/skills/rlm-navigator/SKILL.md:57-125`

**Step 1: Replace the Sub-Agent Delegation section (lines 57-63)**

Replace with:

```markdown
## Sub-Agent Delegation

For large directories with many files, delegate to the `rlm-subcall` agent:
- Give it the `rlm_tree` output and your query
- It will identify which files are relevant
- You then `rlm_map` only those files

**Progress reporting is MANDATORY** — call `rlm_progress` at every phase boundary (see below).
```

**Step 2: Replace the Chunk-Delegate-Synthesize Workflow section (lines 99-125)**

Replace with:

```markdown
## Chunk-Delegate-Synthesize Workflow

For large files or cross-file analysis, use the chunk-delegate pattern with **mandatory progress reporting**:

### 1. Chunk via REPL
```
rlm_progress(event="chunking_start", details={file: "src/large_module.py", query: "<user's question>"})
rlm_repl_exec code="paths = write_chunks('src/large_module.py', size=200, overlap=20); print(paths)"
rlm_progress(event="chunking_complete", details={file: "src/large_module.py", total_chunks: <N>})
```

### 2. Delegate Each Chunk
For each chunk, report progress before and after:
```
rlm_progress(event="chunk_dispatch", details={chunk: <i>, total_chunks: <N>, file: "src/large_module.py", agent: "rlm-subcall"})
```
Send the chunk to the `rlm-subcall` sub-agent with:
- The chunk content (from `peek()` or the chunk file)
- The user's query
- A `chunk_id` (e.g., `"large_module.py:chunk_0"`)

After sub-agent returns:
```
rlm_progress(event="chunk_complete", details={chunk: <i>, total_chunks: <N>, count: <relevant_items>, summary: "<brief>"})
```

If the sub-agent returned `suggested_next_queries`:
```
rlm_progress(event="queries_suggested", details={count: <N>})
```

If the sub-agent returned `answer_if_complete` (non-null):
```
rlm_progress(event="answer_found", details={summary: "<answer preview>"})
```

### 3. Follow Suggested Queries
Execute the highest-priority `suggested_next_queries` to fill gaps from the `missing` field.

### 4. Synthesize
```
rlm_progress(event="synthesis_start", details={count: <total_findings>})
```
Synthesize the collected `relevant` items and buffer contents into a final answer.
```
rlm_progress(event="synthesis_complete", details={summary: "<what was found>"})
```

### When to Use This Workflow
- Files over 500 lines that need thorough analysis
- Cross-file analysis spanning 5+ files
- Architecture understanding tasks ("how does X work end-to-end?")
- When a single `rlm_map` + `rlm_drill` cycle is insufficient

### Enrichment Progress
When dispatching to the `rlm-enricher` sub-agent for semantic summaries:
```
rlm_progress(event="chunk_dispatch", details={file: "<path>", agent: "rlm-enricher", query: "semantic enrichment"})
```
After enrichment returns:
```
rlm_progress(event="chunk_complete", details={file: "<path>", agent: "rlm-enricher", count: <symbols_enriched>, summary: "enriched <N> symbols"})
```
```

**Step 3: Verify the skill file is well-formed**

Read the file back and confirm all sections are present and markdown is valid.

**Step 4: Commit**

```bash
git add .claude/skills/rlm-navigator/SKILL.md
git commit -m "feat: mandate rlm_progress calls in skill workflow instructions"
```

---

### Task 7: E2E integration test

**Files:**
- Modify: `daemon/tests/test_daemon.py`

**Step 1: Write the integration test**

Add to `daemon/tests/test_daemon.py`:

```python
class TestProgressIntegration:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "test.py").write_text("def foo(): pass\n")
        return str(tmp_path)

    def test_full_workflow_progress(self, project):
        """Simulate a full chunk-delegate-synthesize progress flow."""
        cache = SkeletonCache()
        stats = SessionStats()

        events = [
            ("chunking_start", {"file": "big.py", "query": "how does auth work"}),
            ("chunking_complete", {"file": "big.py", "total_chunks": 3}),
            ("chunk_dispatch", {"chunk": 0, "total_chunks": 3, "file": "big.py", "agent": "rlm-subcall"}),
            ("chunk_complete", {"chunk": 0, "total_chunks": 3, "count": 2}),
            ("chunk_dispatch", {"chunk": 1, "total_chunks": 3, "file": "big.py", "agent": "rlm-subcall"}),
            ("chunk_complete", {"chunk": 1, "total_chunks": 3, "count": 0}),
            ("queries_suggested", {"count": 3}),
            ("chunk_dispatch", {"chunk": 2, "total_chunks": 3, "file": "big.py", "agent": "rlm-subcall"}),
            ("chunk_complete", {"chunk": 2, "total_chunks": 3, "count": 1}),
            ("answer_found", {"summary": "Auth in middleware.py"}),
            ("synthesis_start", {"count": 3}),
            ("synthesis_complete", {"summary": "Auth handled via JWT middleware"}),
        ]

        for event, details in events:
            data = json.dumps({"action": "progress", "event": event, "details": details}).encode()
            resp = json.loads(handle_request(data, cache, project, stats=stats))
            assert resp["ok"] is True

        d = stats.to_dict()
        assert d["progress_summary"]["sub_agent_dispatches"] == 3
        assert d["progress_summary"]["chunks_analyzed"] == 3
        assert d["progress_summary"]["answers_found"] == 1
        assert d["progress_summary"]["analyses"] == 3
        assert d["progress_summary"]["enrichments"] == 0
        assert len(d["progress_events"]) == 12

    def test_status_includes_progress(self, project):
        """get_status response includes progress data after events."""
        cache = SkeletonCache()
        stats = SessionStats()
        # Record a progress event
        progress_data = json.dumps({
            "action": "progress", "event": "chunk_dispatch",
            "details": {"file": "main.py", "agent": "rlm-subcall", "chunk": 0, "total_chunks": 1}
        }).encode()
        handle_request(progress_data, cache, project, stats=stats)

        # Now check status
        status_data = json.dumps({"action": "status"}).encode()
        resp = json.loads(handle_request(status_data, cache, project, stats=stats))
        assert resp["session"]["progress_summary"]["sub_agent_dispatches"] == 1
        assert resp["session"]["progress_last_event"]["event"] == "chunk_dispatch"
```

**Step 2: Run the integration test**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestProgressIntegration -v`
Expected: PASS

**Step 3: Run the complete test suite**

Run: `cd daemon && python -m pytest tests/test_daemon.py -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add daemon/tests/test_daemon.py
git commit -m "test: add progress tracking integration tests"
```

---

### Task 8: Final verification

**Step 1: Run all daemon tests**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 2: Build MCP server**

Run: `cd server && npm run build`
Expected: Build succeeds

**Step 3: Run MCP server tests**

Run: `cd server && npx vitest run`
Expected: All tests PASS

**Step 4: Verify skill file is complete**

Read `.claude/skills/rlm-navigator/SKILL.md` and confirm:
- Progress reporting mandate exists in Sub-Agent Delegation section
- Chunk-Delegate-Synthesize section has `rlm_progress` calls at every boundary
- Enrichment Progress subsection exists
- All other existing sections are intact

**Step 5: Final commit if any fixes were needed**

If any adjustments were made during verification, commit them.
