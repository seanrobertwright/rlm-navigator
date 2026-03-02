# RLM Navigator — Recursive Codebase Navigation Skill

## Purpose
Navigate codebases with minimal token usage by treating source files as hierarchical trees of AST skeletons. Never read full files when signatures suffice.

## Workflow — ALWAYS follow this order:

### 1. Health Check
```
get_status
```
If offline or tools return errors, call `get_status` — the MCP server will auto-restart the daemon.

### 2. Explore Structure (never use `ls`, `find`, or `Glob`)
```
rlm_tree path="" max_depth=3
```
See directory layout, file sizes, detected languages. Narrow down to relevant subdirectories.

### 3. Read Signatures (never `cat` or `Read` full files)
```
rlm_map path="src/main.py"
```
See function/class/method signatures + docstrings. Bodies are replaced with `...`.
This tells you WHAT exists without consuming tokens on HOW.

### 4. Surgical Drill (only when you need implementation details)
```
rlm_drill path="src/main.py" symbol="process_data"
```
Reads ONLY the lines of the specific symbol. This is the only time implementation code is loaded.

### 5. Search Across Files
```
rlm_search query="authenticate" path="src/"
```
Find where a symbol is defined across multiple files without reading any of them.

## Rules

- **NEVER read a full file** unless the file is under 50 lines or you've confirmed via `rlm_map` that you need the entire thing.
- **NEVER use `ls`, `find`, `Glob`** for directory exploration — use `rlm_tree`.
- **Map before drill** — always `rlm_map` a file before `rlm_drill` into it.
- **Minimize drill calls** — only drill symbols you actually need to understand or modify.
- **Batch your thinking** — after mapping, decide ALL symbols you need, then drill them in sequence.
- **Prefer skeleton references** — when discussing code, reference signatures from `rlm_map` output rather than drilling just to quote code.

## When NOT to use RLM

- Files you're about to edit entirely (use Read tool directly)
- Config files under 20 lines (just read them)
- Files you've already drilled into this conversation (they're in context)

## Sub-Agent Delegation

For large directories with many files, delegate to the `rlm-subcall` agent:
- Give it the `rlm_tree` output and your query
- It will identify which files are relevant
- You then `rlm_map` only those files

**Progress reporting is MANDATORY** — call `rlm_progress` at every phase boundary (see below).

## REPL-Assisted Analysis

For multi-step analysis that builds on previous findings:

### Setup
```
rlm_repl_init
```
Initializes a stateful Python REPL with helpers: `peek()`, `grep()`, `chunk_indices()`, `write_chunks()`, `add_buffer()`.

### Explore with Helpers
```
rlm_repl_exec code="print(grep('TODO|FIXME|HACK', 'src/'))"
```
Use `peek()` to read specific line ranges, `grep()` for regex search across files.

### Accumulate Findings
```
rlm_repl_exec code="add_buffer('auth_flow', 'Step 1: Token validated in middleware.py:45')"
rlm_repl_exec code="add_buffer('auth_flow', 'Step 2: User loaded in auth.py:92')"
```
Use `add_buffer(key, text)` to collect findings across multiple exec calls.

### Export Results
```
rlm_repl_export
```
Retrieves all accumulated buffers for synthesis.

### Rules
- Initialize REPL at the start of complex analysis sessions
- Use buffers to collect findings rather than relying on context window
- Variables persist between `rlm_repl_exec` calls — assign intermediate results
- Reset with `rlm_repl_reset` when switching to unrelated analysis

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

## Session Summary
Before ending a session or when the user says goodbye, call `get_status` to display token savings.
