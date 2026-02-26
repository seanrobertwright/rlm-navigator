# PageIndex Integration — Design

## Problem

RLM Navigator only indexes source code (via AST squeezing). Non-code files — READMEs, docs, configs, PDFs — are invisible to the navigation tree except as filenames in `rlm_tree`. The LLM can chunk them via `rlm_chunks` but gets no structural overview. This means:

- No skeleton/summary for `.md`, `.rst`, `.txt`, `.pdf` files
- `rlm_map` returns "unsupported language" fallback (first 20 lines) for docs
- `rlm_search` skips non-code files entirely
- `rlm_drill` can't target document sections by name
- The "Codebase as Document" vision from the Mutation Plan is unrealized

Additionally, AST skeletons lack semantic summaries. The LLM must infer meaning from function names and signatures alone during MCTS Selection — no "this class handles JWT auth" hints.

## Goal

A single unified navigation tree covering code AND documents, with LLM-generated semantic enrichment on all nodes, enabling the MCTS/Triad architecture described in the Mutation Plan.

## Approach

### Dual-Provider Architecture

Two LLM providers, each handling what it's best at:

| Provider | Role | When called |
|----------|------|-------------|
| **OpenAI** (via PageIndex library) | Document indexing — build hierarchical ToC trees from `.md`, `.pdf`, `.rst`, `.txt` | On first index + file change |
| **Haiku** (via Anthropic SDK) | Code node enrichment — semantic summaries for AST nodes. MCTS/Triad agent reasoning. | On first index + file change (enrichment). Per-query (agents). |

Both are optional. Core AST navigation works fully offline with zero API calls.

### Key Dependency

`pageindex` Python package added to daemon's `requirements.txt`. Used as a library, not forked. Adapter layer absorbs schema differences between PageIndex output and RLM's unified node format.

## 1. Document Indexing Pipeline

**Trigger:** File watcher detects creation/modification of a document file (`.md`, `.rst`, `.txt`, `.pdf`).

**Flow:**
1. `RLMEventHandler` fires `on_modified` / `on_created`
2. Extension check: if document type → route to `DocumentIndexer` (new class)
3. `DocumentIndexer` calls PageIndex:
   - `.md` → `pageindex.page_index_md.md_to_tree()`
   - `.pdf` → `pageindex.page_index.page_index()`
   - `.txt`, `.rst` → convert to markdown first, then `md_to_tree()`
4. PageIndex returns a hierarchical JSON tree (nodes with `title`, `node_id`, `summary`, child `nodes[]`)
5. `DocumentIndexer` transforms PageIndex output into RLM's unified node format (see Section 4)
6. Result cached in a new `DocumentCache` (parallel to `SkeletonCache`)

**Fallback:** If `pageindex` not installed or no `CHATGPT_API_KEY`, document files fall through to existing `_fallback_squeeze` behavior (first 20 lines).

**Files changed:** `daemon/rlm_daemon.py` (new class + watcher routing), new `daemon/doc_indexer.py`

## 2. Code Node Enrichment

**Trigger:** After a successful `squeeze()` call, if Anthropic API key is available.

**Flow:**
1. `SkeletonCache.get()` returns skeleton string (existing behavior)
2. New `NodeEnricher` class checks if enrichment exists in `EnrichmentCache`
3. If miss: parse skeleton into nodes, batch them, call Haiku to generate 1-line semantic summaries
4. Store enrichments in `EnrichmentCache` (keyed by file path + mtime)
5. `rlm_map` response includes enrichment annotations alongside skeleton

**Prompt pattern:**
```
Given these code signatures from {filename}, provide a 1-line semantic summary for each:
1. class Calculator  # L3-14
2.   def add(self, a: int, b: int) -> int  # L6-8
→ Output: JSON array of {symbol, summary}
```

**Batching:** Group all symbols from one file into a single Haiku call (cheap, fast). Typical file has 5-30 symbols → well within Haiku's context.

**Fallback:** If no `ANTHROPIC_API_KEY`, `rlm_map` returns unenriched skeletons (current behavior).

**Files changed:** new `daemon/node_enricher.py`, modify `daemon/rlm_daemon.py` (integrate into squeeze action)

## 3. New MCP Tools

| Tool | Daemon action | Purpose |
|------|---------------|---------|
| `rlm_doc_map` | `doc_map` | Returns PageIndex-style hierarchical outline for a document file |
| `rlm_doc_drill` | `doc_drill` | Extracts a specific section from a document by `node_id` or section title |
| `rlm_assess` | `assess` | Sufficiency check — given accumulated context, is there enough to answer the query? |

`rlm_doc_map` and `rlm_doc_drill` mirror the code navigation pair (`rlm_map` / `rlm_drill`) but for documents. The LLM uses the same mental model: see structure → pick section → read content → assess.

`rlm_assess` formalizes the iterative retrieval loop from PageIndex's design. It tracks what's been visited and what gaps remain.

**Files changed:** `daemon/rlm_daemon.py` (new actions), `server/src/index.ts` (new tools)

## 4. Unified Node Schema

Both code nodes and document nodes must fit a single format for the tree merge:

```json
{
  "name": "string",
  "type": "file|dir|class|function|section|subsection",
  "source": "ast|pageindex|enrichment",
  "path": "relative/path",
  "range": {"start": 1, "end": 50},
  "summary": "1-line semantic summary (from Haiku or PageIndex)",
  "metadata": {},
  "children": []
}
```

Code-specific metadata: `args`, `is_async`, `decorators`, `imports`
Document-specific metadata: `node_id` (PageIndex), `page_range`, `text_token_count`

The adapter layer in `DocumentIndexer` transforms PageIndex's `{title, node_id, start_index, end_index, summary, nodes[]}` into this format.

## 5. MCTS Session State

The Mutation Plan describes MCTS with Selection/Expansion/Simulation/Backpropagation. This needs session state:

```json
{
  "session_id": "uuid",
  "query": "original user question",
  "visited": ["path/to/file::symbol", "path/to/doc::section"],
  "blacklist": ["path/to/irrelevant_module"],
  "scores": {"path/to/file": 0.8, "path/to/other": 0.2},
  "depth": 3,
  "max_depth": 5,
  "context_accumulated": ["snippet1", "snippet2"]
}
```

**Lifecycle:** Created on first navigation tool call per conversation. Updated on each tool call. Reset on new query. Lives in daemon memory (dict keyed by session ID). No persistence needed — sessions are ephemeral.

**Files changed:** `daemon/rlm_daemon.py` (session state dict + management)

## 6. Triad Agent Architecture

Three specialized agents, all using Haiku:

| Agent | Role | Input | Output |
|-------|------|-------|--------|
| **Explorer** | Proposes navigation paths | Enriched tree + query + session state | Ranked list of nodes to visit |
| **Validator** | Critiques drill results | Implementation code + query + requirements | Accept/reject + reasoning |
| **Orchestrator** | Manages search state | Session state + Explorer/Validator outputs | Next action (drill/backtrack/pivot/answer) |

**Implementation:** These are prompt templates, not separate processes. The daemon stores the templates; the MCP server (or skill layer) coordinates the calls. Each agent call is a Haiku API call with structured JSON output.

**Files changed:** new `daemon/agents/` directory with `explorer.py`, `validator.py`, `orchestrator.py`

## 7. Skill + Template Updates

- `SKILL.md`: Add "Document Navigation" section with the doc-map → doc-drill → assess loop
- `rlm-subcall.md`: Expand to handle document chunks (not just code chunks)
- New `rlm-enricher.md` agent: dedicated to semantic enrichment tasks
- `CLAUDE_SNIPPET.md`: Add `rlm_doc_map`, `rlm_doc_drill`, `rlm_assess` to tool tables

## Failure Modes

| # | Issue | Fix |
|---|---|---|
| 1 | No OpenAI key → document indexing fails | Graceful fallback to `_fallback_squeeze` (first 20 lines) |
| 2 | No Anthropic key → enrichment fails | `rlm_map` returns unenriched skeletons (current behavior) |
| 3 | PageIndex not installed | All document files treated as plain text (existing behavior) |
| 4 | PageIndex API errors (rate limit, timeout) | Retry with backoff; on persistent failure, cache the failure to avoid repeated calls |
| 5 | Haiku API errors | Same retry/cache-failure pattern |
| 6 | Large PDFs → slow indexing | PageIndex handles this natively with node splitting; results cached after first index |
| 7 | MCTS infinite loop | Orchestrator enforces max_depth (default 5) + visited-node deduplication |

## Files to Modify / Create

| File | Changes |
|------|---------|
| `daemon/rlm_daemon.py` | DocumentCache, doc_map/doc_drill/assess actions, session state, watcher routing |
| `daemon/squeezer.py` | Extension detection for document types (route to doc_indexer) |
| `daemon/doc_indexer.py` | **NEW** — PageIndex wrapper, adapter layer, DocumentCache |
| `daemon/node_enricher.py` | **NEW** — Haiku-powered semantic summaries for AST nodes |
| `daemon/agents/explorer.py` | **NEW** — Explorer agent prompt template |
| `daemon/agents/validator.py` | **NEW** — Validator agent prompt template |
| `daemon/agents/orchestrator.py` | **NEW** — Orchestrator agent prompt template |
| `daemon/requirements.txt` | Add `pageindex`, `anthropic` |
| `server/src/index.ts` | New MCP tools: `rlm_doc_map`, `rlm_doc_drill`, `rlm_assess` |
| `.claude/skills/rlm-navigator/SKILL.md` | Document navigation workflow |
| `.claude/agents/rlm-subcall.md` | Document chunk handling |
| `.claude/agents/rlm-enricher.md` | **NEW** — Semantic enrichment agent |
| `templates/CLAUDE_SNIPPET.md` | New tool references |
