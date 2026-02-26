# PageIndex Integration Research — Claude's Analysis

**Date:** 2026-02-26
**Status:** Research / Ideas Only — No Code Written

---

## 1. What Is PageIndex?

PageIndex (by VectifyAI) is a **vectorless, reasoning-based RAG system** for documents. Instead of embedding chunks into a vector DB, it:

1. **Builds a hierarchical JSON tree index** (Table of Contents) from a document
2. **Uses LLM reasoning to navigate** that tree — iteratively selecting sections, reading content, and deciding "where to look next"
3. Achieves 98.7% accuracy on FinanceBench, outperforming vector RAG

Key properties:
- No vector database, no embeddings, no chunking
- Tree nodes contain: `title`, `node_id`, `start_index`/`end_index`, `summary`, recursive `nodes[]`
- The LLM "reads" the ToC, picks sections, extracts content, decides if it has enough, and loops
- Available as: open-source Python lib, hosted API, MCP server (`@pageindex/mcp`)

**Source:** [PageIndex GitHub](https://github.com/VectifyAI/PageIndex) | [Intro Article](https://pageindex.ai/blog/pageindex-intro) | [MCP Server](https://github.com/VectifyAI/pageindex-mcp)

---

## 2. Where RLM Navigator and PageIndex Converge

The Mutation Plan & PRD already identified PageIndex as the "document-world sibling" of RLM Navigator. The philosophical alignment is strong:

| Concept | PageIndex (Documents) | RLM Navigator (Codebases) |
|---------|----------------------|---------------------------|
| **Index type** | JSON ToC tree from document structure | JSON skeleton tree from AST structure |
| **Node content** | Title + summary + page range | Signature + docstring + line range |
| **Retrieval** | LLM reasons over ToC, requests sections | LLM reasons over skeleton, drills into symbols |
| **No vectors** | Yes | Yes |
| **No chunking** | Yes (natural sections) | Yes (natural AST boundaries) |
| **Live updates** | N/A (static docs) | File watcher daemon with cache invalidation |
| **MCP integration** | Yes (`pageindex-mcp`) | Yes (`rlm-navigator` MCP server) |

The core loop is nearly identical:
1. See structure → 2. Reason about where to look → 3. Extract content → 4. Assess sufficiency → 5. Loop or answer

---

## 3. Integration Ideas (Organized by Ambition Level)

### Level 1: Steal Patterns (Low effort, high value)

**A. Adopt PageIndex's node enrichment pipeline for AST nodes**
- PageIndex adds `summary`, `node_id`, and `doc_description` to tree nodes
- RLM Navigator's squeezer currently outputs signatures + docstrings but no LLM-generated summaries
- **Idea:** After squeezing, run Haiku to generate 1-line summaries per class/function node. This would help the "Selection" phase of MCTS — the LLM has more signal to pick the right branch
- **Tradeoff:** Extra tokens/latency during indexing vs. better navigation accuracy

**B. Adopt the iterative retrieval loop pattern**
- PageIndex's retrieval is explicitly multi-turn: "Do I have enough? No → select more sections"
- RLM Navigator's Reasoning Skill already does this informally (map → drill → backtrack)
- **Idea:** Formalize this as a structured protocol in the MCP server — add a `rlm_assess` tool that the LLM calls to evaluate "do I have enough context to answer the user's question?"
- This would make the navigation loop self-terminating rather than relying on the LLM's judgment

**C. Port the `post_processing` / `list_to_tree` utilities**
- PageIndex has clean utilities for converting flat node lists into nested trees
- Could be useful if we want to expose the full codebase as a single hierarchical JSON tree (directory → file → class → function) rather than file-by-file

### Level 2: Hybrid Architecture (Medium effort)

**D. Unified tree format for code AND docs**
- A codebase isn't just code — it has READMEs, docs, configs, comments
- **Idea:** Use PageIndex (as a library dependency) to index `.md`, `.rst`, `.txt`, `.pdf` files alongside RLM's AST squeezer for code files. Merge into one navigable tree
- The LLM would navigate the entire project knowledge (code + docs) through a single hierarchical index

**E. Cross-reference following**
- PageIndex can follow internal document references ("see Appendix G")
- Codebases have the equivalent: imports, function calls, type references
- **Idea:** Enrich AST nodes with cross-reference metadata (import graph, call graph). When the LLM drills into a function and sees it calls `auth.validate_token()`, the system could offer "Navigate to auth.validate_token?" as a structured next step
- This is partially what the MCTS "Expansion" phase does, but making it explicit in the node metadata would help

**F. PageIndex MCP as a companion tool**
- Register both `rlm-navigator` AND `pageindex-mcp` as MCP servers
- The LLM uses RLM for code navigation and PageIndex for document queries
- They share the same "reasoning over tree structures" mental model
- **Tradeoff:** Two MCP servers = more tool surface area = more token overhead in tool descriptions

### Level 3: Deep Fusion (High effort, research-grade)

**G. Replace squeezer output with PageIndex-compatible format**
- Reformat the AST skeleton JSON to match PageIndex's node schema exactly (`structure`, `title`, `node_id`, `start_index`, `end_index`, `summary`, `nodes[]`)
- This would let you use PageIndex's retrieval logic (or a mutation of it) directly on code
- **Tradeoff:** PageIndex's retrieval logic is designed for natural language documents; code has different structural semantics

**H. LLM-powered "semantic squeeze"**
- PageIndex uses LLMs to generate summaries and structure from unstructured text
- **Idea:** After AST squeezing (free, local), run a Haiku pass to generate semantic summaries: "This class handles JWT authentication with Redis-backed session storage"
- These summaries become part of the cached index (updated only when files change)
- The MCTS Selection phase would dramatically improve — the LLM isn't guessing from names alone

**I. The "Codebase as Document" vision from the original conversation**
- Treat the entire repo as one "large document" with a PageIndex-style hierarchical index
- Root = project description, Branches = modules with summaries, Leaves = function implementations
- The full tree structure lives in the LLM's context (the "in-context indexing" pattern)
- Navigation happens through conversation turns, not just tool calls

---

## 4. Design Decisions (Resolved)

Answers provided by project owner on 2026-02-26:

| # | Question | Decision | Implications |
|---|----------|----------|-------------|
| 1 | **Local-only vs. LLM-augmented indexing?** | **LLM-augmented using Haiku** | Node summaries, semantic enrichment, and document indexing will use Haiku API calls. Results cached and invalidated on file change. Indexing is no longer zero-cost but gains dramatically better navigation signal. |
| 2 | **Code-only or code+docs?** | **Index everything** | All file types in the project get indexed — code via AST squeezer, documents via PageIndex, configs/other via appropriate parsers. Single unified navigable tree. |
| 3 | **Companion or fusion?** | **Fuse, but as a dependency** | PageIndex is integrated into RLM Navigator as a library/API dependency, not a separate MCP server. When PageIndex updates, RLM Navigator pulls in improvements. Single MCP surface. |
| 4 | **Node summaries — when and how?** | **Best approach TBD (see Section 5)** | Claude recommends: generate on first index + invalidate/regenerate on file change. Haiku calls are cheap enough to run per-node. Cache in daemon's existing cache layer. |
| 5 | **Use PageIndex as library or reimplement?** | **Depend on it as a library/API** | `pip install pageindex` (or equivalent) becomes a daemon dependency. Use their indexing pipeline for documents, their tree utilities for format conversion. Benefit from upstream improvements. |
| 6 | **MCTS formalization timing?** | **Yes, now — alongside PageIndex integration** | PageIndex integration is the catalyst to formalize the MCTS loop (Selection/Expansion/Simulation/Backpropagation) and the Triad agents (Explorer/Validator/Orchestrator) in shipped code. |
| 7 | **Near-term goal?** | **Ship a tangible improvement** | This is not a research prototype. The integration should result in a shippable version of RLM Navigator with measurably better navigation capabilities. |
| 8 | **Multi-agent still the plan?** | **Yes** | Explorer/Validator/Orchestrator architecture proceeds. PageIndex's retrieval logic maps to the Explorer agent. Haiku sub-agent role expands to cover both code validation and document retrieval. |

---

## 5. Revised Integration Strategy

Given the decisions above, the strategy collapses from "pick a level" to a unified roadmap:

### Phase 1: PageIndex as Dependency + Document Indexing
- Add `pageindex` as a Python dependency in the daemon
- Extend the file watcher to detect non-code files (`.md`, `.rst`, `.txt`, `.pdf`)
- Route document files through PageIndex's `md_to_tree()` / `page_index()` pipeline
- Route code files through the existing AST squeezer
- Merge both into a single hierarchical tree per project
- Cache all results in the daemon's existing cache layer

### Phase 2: Haiku-Powered Node Enrichment
- After AST squeezing, run a Haiku pass on each node to generate semantic summaries
- Store summaries in the cached skeleton alongside signatures/docstrings
- Invalidate and regenerate summaries when files change (daemon watcher already handles this)
- Expose enriched nodes through existing MCP tools (`rlm_map`, `rlm_drill`)

### Phase 3: MCTS Formalization + Retrieval Loop
- Formalize the MCTS loop as a daemon-side state machine (not just a skill prompt)
- Add `rlm_assess` MCP tool for sufficiency checking
- Implement backpropagation: when a branch is rejected, mark it "Low Signal" in the session state
- The skill/agent layer drives the loop; the daemon maintains the search state

### Phase 4: Triad Agent Architecture
- Explorer agent: uses enriched tree + PageIndex retrieval patterns to propose navigation paths
- Validator agent: surgically reads implementations, critiques against requirements
- Orchestrator agent: manages search history, blacklist, backtracking decisions
- All three coordinate through the daemon's session state (not through LLM context)

### Key Architectural Principle
**PageIndex handles document understanding. The AST squeezer handles code structure. The daemon unifies them into one tree. The MCTS loop navigates it. The Triad agents reason over it.**

---

## 6. Dual-Provider LLM Strategy (Resolved)

**Decision: Keep both OpenAI and Haiku. Each provider handles what it's best at.**

### Provider Assignments

| Component | Provider | Reason |
|-----------|----------|--------|
| Document indexing (PDF, markdown, text) | **OpenAI (via PageIndex)** | PageIndex prompts are tuned and tested for GPT-4o. Running stock PageIndex avoids patching/drift. |
| Code node semantic summaries | **Haiku (Anthropic)** | Cheap, fast, already specified for sub-agent work in RLM Navigator. |
| MCTS / Triad agents | **Haiku (Anthropic)** | Consistent with existing sub-agent architecture. |

### Why This Works

- **Clean separation** — PageIndex handles documents (OpenAI), daemon handles code enrichment (Haiku). They never call each other. Outputs merge into one unified tree.
- **PageIndex stays stock** — no monkey-patching, no forking LLM calls. Dependency stays clean.
- **Caching neutralizes cost** — both providers only get called on first index or file change. Day-to-day navigation is free (served from daemon cache).

### Known Tradeoffs

| Concern | Impact | Mitigation |
|---------|--------|------------|
| **Two API keys required** | Users need both `CHATGPT_API_KEY` and `ANTHROPIC_API_KEY` for full functionality | Both are optional — code navigation works without either (AST squeezing is free/local). Document indexing requires OpenAI key. Code enrichment requires Anthropic key. |
| **Two billing accounts** | Harder to predict total cost | Caching keeps costs low. Document indexing is one-time per file. Code summaries are one-time per symbol. |
| **Token counting divergence** | PageIndex uses `tiktoken` (OpenAI tokenizer), Haiku uses Claude's tokenizer. A "20k token node" means different things to each. | Only matters if we unify token budgets across the tree. For now, each provider manages its own limits independently. |
| **Offline mode degradation** | Current RLM Navigator works fully offline. Adding providers means some features need internet. | Graceful fallback: no OpenAI key → skip document indexing. No Anthropic key → skip semantic summaries. Core AST navigation always works offline. |

### Config Structure

```
# .env or .rlm/config
CHATGPT_API_KEY=sk-...          # For PageIndex document indexing
ANTHROPIC_API_KEY=sk-ant-...    # For Haiku code enrichment + agents

# Optional: PageIndex model override (default: gpt-4o-2024-11-20)
PAGEINDEX_MODEL=gpt-4o-2024-11-20
```

---

## 7. Dependency & Update Strategy

Since PageIndex is a dependency (not a fork):

| Concern | Approach |
|---------|----------|
| **Installation** | `pageindex` added to daemon's `requirements.txt` |
| **Version pinning** | Pin to a specific version; upgrade deliberately |
| **API surface used** | `md_to_tree()`, `page_index()`, tree utilities (`list_to_tree`, `get_nodes`, `write_node_id`) |
| **Upstream changes** | Periodic review of PageIndex releases; pull in improvements that benefit document indexing |
| **Breaking changes** | Adapter layer between PageIndex output and RLM's unified tree format absorbs schema changes |
| **Fallback** | If PageIndex is unavailable (not installed, API down), document files are skipped — code navigation still works at full capability |

---

## 8. Key Takeaways

- PageIndex validates the core thesis of RLM Navigator: **hierarchical tree indexing + LLM reasoning beats vector RAG**
- The two systems are **fused** — PageIndex handles document indexing, RLM handles code, both feed a unified tree
- **Dual-provider strategy**: OpenAI powers document indexing (via stock PageIndex), Haiku powers code enrichment and agent reasoning
- Both providers are **optional** — core AST navigation works fully offline with zero API calls
- PageIndex integration is the **catalyst for MCTS formalization** and the **Triad agent architecture**
- The goal is a **shippable improvement**, not a research exercise
- PageIndex is a **library dependency** — RLM Navigator benefits from upstream improvements without maintaining a fork

---

## 9. Open Design Questions (For Next Session)

These are implementation-level questions to resolve when we start coding:

1. **Unified node schema:** What's the merged format that accommodates both AST nodes (line ranges, args, decorators) and document nodes (page ranges, summaries)? Need a superset schema.
2. **Haiku API integration:** How does the daemon call Haiku? Direct Anthropic SDK (`anthropic` pip package)? Need to decide API path and key management within the daemon process.
3. **Session state persistence:** MCTS search state (visited nodes, blacklist, scores) needs to persist across tool calls but reset per user query. Where does this live — daemon memory, temp file, MCP session?
4. **Tree merge strategy:** When code and docs both contribute nodes, how do they interleave? Separate subtrees under root? Or interleaved by directory (README.md sits next to its sibling .py files)?
5. **Graceful degradation UX:** When a user has no API keys, how does the system communicate what's available vs. what's degraded? Silent fallback? Warning on `get_status`?
