# RLM Navigator — Manual Testing Checklist

> **Instructions**: Copy this file or edit it directly. Fill in the session info below, then work through each test. Check the boxes as you go. Add notes in the **Notes** column or beneath each section.

---

## Session Info

| Field               | Value                          |
|---------------------|--------------------------------|
| **Date**            | `YYYY-MM-DD`                   |
| **Tester**          |                                |
| **RLM Version**     |                                |
| **Target Repo**     |                                |
| **Target Repo URL** |                                |
| **Repo Size**       | ___ files / ___ lines (approx) |
| **OS / Platform**   |                                |
| **Claude Client**   | Claude Code / Other            |

---

## Prerequisites

- [ ] RLM Navigator installed in target project (`npx rlm-navigator@latest install`)
- [ ] Project opened in Claude Code
- [ ] Daemon auto-started on connection (or started manually)
- [ ] `get_status` confirms daemon is ALIVE with correct project root
- [ ] _(Optional)_ `ANTHROPIC_API_KEY` set for Haiku enrichment tests (Test 13)
- [ ] _(Optional)_ `CHATGPT_API_KEY` set for PageIndex document indexing (Test 5)

---

## Test Results

### Test 1: Directory Exploration (`rlm_tree`)

_Verify Claude uses `rlm_tree` instead of `ls`/`find`/`Glob` for directory exploration._

**Prompt**: _"Show me the project structure."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 1.1 | Claude calls `rlm_tree` (not `ls`, `find`, or `Glob`) | [ ] | |
| 1.2 | Output shows directories with item counts | [ ] | |
| 1.3 | Files show sizes and detected languages | [ ] | |
| 1.4 | Hidden/ignored dirs excluded (`.git`, `node_modules`, `__pycache__`) | [ ] | |
| 1.5 | Response stays within truncation budget (no runaway output) | [ ] | |

**Follow-up prompt**: _"What's inside the `src/` directory?"_ (substitute a real directory)

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 1.6 | Claude scopes tree to subdirectory (`path="src/"`) | [ ] | |
| 1.7 | Deeper nesting visible within the focused subtree | [ ] | |

---

### Test 2: File Signatures (`rlm_map`)

_Verify Claude reads structural skeletons instead of full files._

**Prompt**: _"What functions and classes are in `<file>`?"_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 2.1 | Claude calls `rlm_map` (not `cat`, `Read`, or `head`) | [ ] | |
| 2.2 | Output shows class/function/method signatures with line ranges | [ ] | |
| 2.3 | Docstrings preserved, implementation bodies show `...` (elided) | [ ] | |
| 2.4 | No raw source code appears — skeleton only | [ ] | |

**Observation** _(fill in)_:
- File line count (from `rlm_tree`): ___
- Skeleton line count: ___
- Compression ratio: ___x

---

### Test 3: Surgical Drill (`rlm_drill`)

_Verify Claude retrieves a single symbol's implementation without reading the entire file._

**Prompt**: _"Show me the implementation of `<function_name>` in `<file>`."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 3.1 | Claude calls `rlm_map` first to locate the symbol | [ ] | |
| 3.2 | Then calls `rlm_drill` with the exact symbol name | [ ] | |
| 3.3 | Output shows only the targeted function with line numbers (e.g., `L45-82`) | [ ] | |
| 3.4 | No surrounding functions or unrelated code appears | [ ] | |

**Edge case prompt**: _"Show me the `nonexistent_function` in `<file>`."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 3.5 | Claude reports error cleanly (no full-file read fallback) | [ ] | |

---

### Test 4: Cross-File Search (`rlm_search`)

_Verify symbol discovery across the entire codebase._

**Prompt**: _"Find all files that reference `<common_symbol>` in this project."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 4.1 | Claude calls `rlm_search` (not `grep` or `Grep`) | [ ] | |
| 4.2 | Results show file paths with matching skeleton lines | [ ] | |
| 4.3 | Multiple files returned if symbol appears across codebase | [ ] | |
| 4.4 | No full file contents loaded — skeleton excerpts only | [ ] | |

**Follow-up prompt**: _"Drill into the most relevant one."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 4.5 | Claude completes the **search -> map -> drill** workflow | [ ] | |

---

### Test 5: Document Navigation (`rlm_doc_map` + `rlm_doc_drill`)

_Verify structured navigation of documentation files._

**Prompt**: _"What sections are in the README?"_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 5.1 | Claude calls `rlm_doc_map` on the markdown file | [ ] | |
| 5.2 | Output is a hierarchical section tree with titles and line ranges | [ ] | |
| 5.3 | Nested headings (`##`, `###`) appear as children of parents | [ ] | |

**Follow-up prompt**: _"Show me the Installation section."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 5.4 | Claude calls `rlm_doc_drill` with the section title | [ ] | |
| 5.5 | Only that section's content returned, not the entire document | [ ] | |

---

### Test 6: Context Sufficiency (`rlm_assess`)

_Verify the assessment tool guides navigation decisions._

**Prompt**: _"How does authentication work in this project?"_ (or any broad architectural question)

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 6.1 | After gathering context, Claude calls `rlm_assess` | [ ] | |
| 6.2 | Assessment confirms sufficiency or suggests further exploration | [ ] | |
| 6.3 | Claude follows the assessment's guidance | [ ] | |

---

### Test 7: REPL-Assisted Analysis (`rlm_repl_*`)

_Verify the stateful REPL for targeted analysis workflows._

**Prompt**: _"Use the REPL to find all TODO comments across the codebase and summarize them."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 7.1 | Claude calls `rlm_repl_init` to start a fresh session | [ ] | |
| 7.2 | Uses `rlm_repl_exec` with `grep("TODO")` to search | [ ] | |
| 7.3 | Uses `peek()` to read context around specific matches | [ ] | |
| 7.4 | Uses `add_buffer("todos", ...)` to accumulate findings | [ ] | |
| 7.5 | Calls `rlm_repl_export` to retrieve collected results | [ ] | |
| 7.6 | Analysis uses minimal tokens compared to full-file reads | [ ] | |

**Staleness sub-test** _(requires manual file edit)_:

1. Ask Claude to grep for something and store the result
2. Manually edit the file that was found
3. Ask Claude to check REPL status

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 7.7 | `rlm_repl_status` shows staleness warning for modified file | [ ] | |

---

### Test 8: File Chunking (`rlm_chunks` + `rlm_chunk`)

_Verify large file handling via chunked reading._

**Prompt**: _"How many chunks does `<large_file>` have? Show me the first chunk."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 8.1 | Claude calls `rlm_chunks` for metadata (total, line count, size, overlap) | [ ] | |
| 8.2 | Calls `rlm_chunk` with index 0 to read first chunk | [ ] | |
| 8.3 | Chunk content includes header with line range (e.g., "lines 1-200") | [ ] | |
| 8.4 | Subsequent chunks readable independently | [ ] | |

---

### Test 9: Full Navigation Workflow (End-to-End)

_Verify the complete **tree -> map -> drill -> edit** workflow in a realistic task._

**Prompt**: _"Find where HTTP request validation happens and add input length checking to the main handler."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 9.1 | **Tree**: Claude explores project structure to find relevant dirs | [ ] | |
| 9.2 | **Search/Map**: Searches for validation symbols, maps candidates | [ ] | |
| 9.3 | **Drill**: Drills into the specific handler function | [ ] | |
| 9.4 | **Edit**: Makes surgical edit using only drilled lines | [ ] | |
| 9.5 | At no point does Claude `cat`/`Read` an entire file | [ ] | |
| 9.6 | Edit targets specific lines, not a full-file rewrite | [ ] | |

---

### Test 10: Token Savings Verification

_Quantify the actual token reduction achieved during the session._

**Prompt** _(after completing prior tests)_: _"Show me the session statistics."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 10.1 | Claude calls `get_status` | [ ] | |
| 10.2 | **Tokens served** total displayed | [ ] | |
| 10.3 | **Tokens avoided** total displayed | [ ] | |
| 10.4 | **Reduction percentage** shown (expect 60-90% on real codebases) | [ ] | |
| 10.5 | **Tool call breakdown** per-tool (rlm_map, rlm_drill, etc.) | [ ] | |

**Observed stats** _(fill in)_:

| Metric | Value |
|--------|-------|
| Tokens served | |
| Tokens avoided | |
| Reduction % | |
| Total tool calls | |

**Optional benchmark** _(run from terminal)_:
```bash
python benchmark.py --root . --query "<common_symbol>"
python benchmark.py --root . --query "<common_symbol>" --mode repl
python benchmark.py --root . --file "<large_file>" --mode chunks
```

---

### Test 11: File Watcher Integration

_Verify that code changes are reflected without restarting the daemon._

**Steps**:
1. Map a file: _"Show me the skeleton of `<file>`."_
2. In a separate editor, add a new function to that file and save
3. Map the same file again

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 11.1 | Second `rlm_map` shows the newly added function | [ ] | |
| 11.2 | No daemon restart was needed | [ ] | |

---

### Test 12: Multi-Language Support

_Verify AST parsing works across supported languages._

**Prompt**: _"Map one Python file, one JavaScript file, and one TypeScript file."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 12.1 | **Python**: `class`, `def`, `async def`, decorators appear | [ ] | |
| 12.2 | **JavaScript**: `class`, `function`, arrow functions, `export` | [ ] | |
| 12.3 | **TypeScript**: `interface`, `type`, `class`, `function`, generics | [ ] | |
| 12.4 | Unsupported types (`.toml`, `.yaml`) get graceful fallback (first 20 lines + count) | [ ] | |

---

### Test 13: Haiku Enrichment Verbosity

_Verify that Haiku enrichment activity is visible throughout the system._

**Prerequisites**: `ANTHROPIC_API_KEY` set, `anthropic` SDK installed.

#### 13a: Status Reports Enrichment Availability

**Prompt**: _"Check if RLM Navigator is running."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 13a.1 | `get_status` response includes `enrichment_available: true` | [ ] | |
| 13a.2 | Without API key/SDK, reports `enrichment_available: false` (no errors) | [ ] | |

#### 13b: Progress Notifications During Navigation

**Prompt**: _"Find and explain the authentication flow in this project."_ (or any broad multi-file question)

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 13b.1 | `[RLM] Chunking <file>...` messages appear | [ ] | |
| 13b.2 | `[RLM] Dispatching chunk N/M of <file> to rlm-enricher...` appears | [ ] | |
| 13b.3 | `[RLM] chunk N/M complete — X relevant symbols found` appears | [ ] | |
| 13b.4 | `get_status` shows Sub-agent Activity dispatches | [ ] | |
| 13b.5 | `get_status` shows `Last:` with most recent progress message | [ ] | |

#### 13c: Enriched Skeleton Annotations

**Prompt**: _"Map `<file_with_many_functions>`."_ (pick a file with 5+ functions)

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 13c.1 | Skeleton lines include `# <summary>` annotations after line ranges | [ ] | |
| 13c.2 | Summaries describe **what the symbol does**, not just its type | [ ] | |
| 13c.3 | Without `ANTHROPIC_API_KEY`, skeleton returns clean (no errors/placeholders) | [ ] | |

**Example expected output**:
```
def validate_token(self, token: str) -> bool:  # L25-30  # Checks if token starts with 'sk-' prefix.
class AuthManager:  # L1-80  # Manages JWT-based authentication lifecycle.
```

#### 13d: Enrichment Cache Behavior

**Steps**:
1. Map a file (first call — triggers Haiku enrichment)
2. Map the same file again immediately

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 13d.1 | Second call returns instantly with identical annotations (cache hit) | [ ] | |
| 13d.2 | After editing the file, re-mapping produces fresh annotations (cache invalidated) | [ ] | |

#### 13e: Background Enrichment Worker

**Prompt**: _"Show me the project structure, then map the 3 largest Python files."_

| # | Criterion | Pass | Notes |
|---|-----------|:----:|-------|
| 13e.1 | `rlm_map` responds without blocking on enrichment | [ ] | |
| 13e.2 | First call may return skeleton without annotations (worker still processing) | [ ] | |
| 13e.3 | Subsequent call to same file shows annotations (worker completed) | [ ] | |
| 13e.4 | `get_status` reflects enrichment worker activity in session stats | [ ] | |

---

## Summary

| Test | Description | Result | Notes |
|------|-------------|:------:|-------|
| 1 | Directory Exploration (`rlm_tree`) | [ ] | |
| 2 | File Signatures (`rlm_map`) | [ ] | |
| 3 | Surgical Drill (`rlm_drill`) | [ ] | |
| 4 | Cross-File Search (`rlm_search`) | [ ] | |
| 5 | Document Navigation (`rlm_doc_map` / `rlm_doc_drill`) | [ ] | |
| 6 | Context Sufficiency (`rlm_assess`) | [ ] | |
| 7 | REPL-Assisted Analysis (`rlm_repl_*`) | [ ] | |
| 8 | File Chunking (`rlm_chunks` / `rlm_chunk`) | [ ] | |
| 9 | Full Navigation Workflow (E2E) | [ ] | |
| 10 | Token Savings Verification | [ ] | |
| 11 | File Watcher Integration | [ ] | |
| 12 | Multi-Language Support | [ ] | |
| 13 | Haiku Enrichment Verbosity | [ ] | |

**Overall Result**: ___ / 13 tests passed

---

## Troubleshooting Quick Reference

| Symptom | Fix |
|---------|-----|
| `get_status` shows OFFLINE | `npx rlm-navigator status` or restart: `python daemon/rlm_daemon.py --root .` |
| `rlm_map` returns fallback for supported language | `pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript` |
| Claude uses `Read`/`cat` instead of RLM tools | Verify `.claude/skills/rlm-navigator/SKILL.md` exists in your project |
| Stale data after file edits | Verify file watcher is active via `get_status` |
| Port conflict on startup | `RLM_DAEMON_PORT=9200 python daemon/rlm_daemon.py --root .` |
| Haiku enrichment not appearing | Verify `ANTHROPIC_API_KEY` is set + `pip install anthropic`. Check `get_status` for `enrichment_available: true` |
| Enrichment annotations stale after edit | Re-map the file — `EnrichmentCache` invalidates on mtime change |

---

## Additional Notes

_Use this space for observations, bugs found, or improvement ideas._

```




```
