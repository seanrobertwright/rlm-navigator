<!-- rlm-navigator:start -->

## RLM Navigator — Token Conservation Rules

1. **Never read full files** — use `rlm_map` for signatures, `rlm_drill` for specific symbols
2. **Never use ls/find/Glob** for exploration — use `rlm_tree`
3. **Map before drill** — always see the skeleton first
4. **Surgical edits only** — use Edit tool with minimal context
5. **Minimalist responses** — no verbose explanations unless asked
6. **No redundant reads** — if it's in context, don't read it again

### Navigation Tools

| Tool | Purpose |
|------|---------|
| `get_status` | Check daemon health |
| `rlm_tree` | See directory structure (replaces ls/find) |
| `rlm_map` | See file signatures only (replaces cat/read) |
| `rlm_drill` | Read specific symbol implementation |
| `rlm_search` | Find symbols across files |

### REPL Tools

| Tool | Purpose |
|------|---------|
| `rlm_repl_init` | Initialize the stateful REPL |
| `rlm_repl_exec` | Execute Python code (variables persist) |
| `rlm_repl_status` | Check variables, buffers, execution count |
| `rlm_repl_reset` | Clear all REPL state |
| `rlm_repl_export` | Export accumulated buffers |

### Built-in REPL Helpers

| Helper | Signature | Purpose |
|--------|-----------|---------|
| `peek` | `peek(file_path, start=1, end=None)` | Read numbered lines from file |
| `grep` | `grep(pattern, path=".", max_results=50)` | Regex search across files |
| `chunk_indices` | `chunk_indices(file_path, size=200, overlap=20)` | Compute chunk boundaries |
| `write_chunks` | `write_chunks(file_path, out_dir=None, size=200, overlap=20)` | Write chunks to disk |
| `add_buffer` | `add_buffer(key, text)` | Accumulate findings in named buffers |

<!-- rlm-navigator:end -->
