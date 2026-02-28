# Sub-Agent Progress Visibility — Design

**Date:** 2026-02-28
**Status:** Approved
**Scope:** MCP tool + daemon tracking + skill instructions

## Problem

When Haiku sub-agents (rlm-subcall, rlm-enricher) perform work during chunk-delegate-synthesize or enrichment workflows, the user has no visual feedback in Claude Code beyond a generic Agent tool spinner. There's no indication of what's being analyzed, which chunk is being processed, or how far along the pipeline is.

## Solution

A three-layer approach:

1. **New `rlm_progress` MCP tool** — called by the orchestrating model at each workflow boundary, producing a visible tool-call line in Claude Code's UI
2. **Daemon session tracking** — progress events stored in session stats, surfaced via `get_status`
3. **Skill instruction mandates** — SKILL.md updated to require `rlm_progress` calls at each phase transition

## Components

### 1. `rlm_progress` MCP Tool

**Signature:**

```typescript
rlm_progress(
  event: "chunking_start" | "chunking_complete" | "chunk_dispatch"
       | "chunk_complete" | "synthesis_start" | "synthesis_complete"
       | "answer_found" | "queries_suggested",
  details: {
    file?: string,          // file being processed
    agent?: string,         // "rlm-subcall" | "rlm-enricher"
    chunk?: number,         // current chunk index (0-based)
    total_chunks?: number,  // total chunks in batch
    query?: string,         // the user query being investigated
    count?: number,         // number of items (queries, symbols, etc.)
    summary?: string        // brief result summary
  }
)
```

**Returns:** Formatted human-readable status line, e.g.:
- `[RLM] Dispatching chunk 3/7 of main.py to rlm-subcall...`
- `[RLM] Chunk 3/7 complete — 2 relevant symbols found, 3 queries suggested`
- `[RLM] Synthesis starting — analyzing 12 findings across 7 chunks`
- `[RLM] Enriching main.py via rlm-enricher...`

**Behavior:** Formats the message, sends a `progress` action to the daemon, returns the formatted message as tool output.

### 2. Daemon Session Tracking

New fields in the existing session dict:

```python
session["progress_events"] = []          # list of {event, details, timestamp}
session["progress_summary"] = {
    "sub_agent_dispatches": 0,           # total chunk_dispatch events
    "chunks_analyzed": 0,                # total chunk_complete events
    "answers_found": 0,                  # total answer_found events
    "enrichments": 0,                    # dispatches where agent="rlm-enricher"
    "analyses": 0,                       # dispatches where agent="rlm-subcall"
}
```

New daemon action: `progress`
- Receives `{action: "progress", event: str, details: dict}`
- Appends to `progress_events` with timestamp
- Increments appropriate `progress_summary` counters
- Returns `{ok: true}`

### 3. `get_status` Enhancement

When `progress_summary` has non-zero values, `get_status` output includes:

```
Sub-agent Activity:
  Dispatches: 7 (4 chunk analysis, 3 enrichment)
  Chunks analyzed: 4 | Answers found: 1
  Last event: chunk_complete — main.py chunk 4/7
```

### 4. Skill Instructions

#### Chunk-Delegate-Synthesize Workflow

Updated flow in SKILL.md:

```
1. rlm_progress(event="chunking_start", details={file, query})
2. rlm_chunks / rlm_repl_exec to chunk the file
3. rlm_progress(event="chunking_complete", details={file, total_chunks})

For each chunk:
4. rlm_progress(event="chunk_dispatch", details={chunk, total_chunks, file, agent="rlm-subcall"})
5. Agent tool → Haiku sub-agent analyzes chunk
6. rlm_progress(event="chunk_complete", details={chunk, total_chunks, count, summary})
7. If suggested queries: rlm_progress(event="queries_suggested", details={count})
8. If answer found: rlm_progress(event="answer_found", details={summary})

After all chunks:
9.  rlm_progress(event="synthesis_start", details={count})
10. Synthesize findings
11. rlm_progress(event="synthesis_complete", details={summary})
```

#### Enrichment Workflow

```
1. rlm_progress(event="chunk_dispatch", details={file, agent="rlm-enricher", query="semantic enrichment"})
2. Agent tool → Haiku rlm-enricher analyzes skeleton
3. rlm_progress(event="chunk_complete", details={file, agent="rlm-enricher", count, summary="enriched N symbols"})
```

## Files Changed

| File | Change |
|------|--------|
| `server/src/index.ts` | New `rlm_progress` tool; enhanced `get_status` formatting |
| `daemon/rlm_daemon.py` | New `progress` action handler; session tracking fields |
| `.claude/skills/rlm-navigator/SKILL.md` | Mandatory progress calls in chunk-delegate-synthesize and enrichment sections |

## Files NOT Changed

- `.claude/agents/rlm-subcall.md` — returns JSON, doesn't call tools
- `.claude/agents/rlm-enricher.md` — returns JSON, doesn't call tools
- `daemon/squeezer.py` — no involvement
- `daemon/rlm_repl.py` — no involvement

## Event Types Reference

| Event | When | Key Details |
|-------|------|-------------|
| `chunking_start` | Before chunking a file | file, query |
| `chunking_complete` | After chunks are ready | file, total_chunks |
| `chunk_dispatch` | Before sending chunk to sub-agent | chunk, total_chunks, file, agent |
| `chunk_complete` | After sub-agent returns | chunk, total_chunks, count, summary |
| `queries_suggested` | Sub-agent suggested follow-up queries | count |
| `answer_found` | Sub-agent found complete answer | summary |
| `synthesis_start` | Before synthesizing all findings | count (total findings) |
| `synthesis_complete` | After synthesis is done | summary |
