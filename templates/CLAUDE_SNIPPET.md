<!-- rlm-navigator:start -->

## REQUIRED: Use RLM Navigator for All Codebase Navigation

This project uses the `/rlm-navigator` skill. You MUST follow the RLM Navigator workflow for ALL codebase exploration, file reading, and code search. This is NOT optional.

**Mandatory workflow: `rlm_tree` → `rlm_map` → `rlm_drill` → Edit**

### What you MUST do:
- Use `rlm_tree` instead of `ls`, `find`, or `Glob` for directory exploration
- Use `rlm_map` instead of `Read` or `cat` to understand file contents
- Use `rlm_drill` to read only the specific symbol you need
- Use `rlm_search` to find symbols across files

### What you MUST NOT do:
- Do NOT read full files with the `Read` tool unless the file is under 50 lines or is a config file
- Do NOT use `Glob` or `find` to explore the project structure
- Do NOT use `grep` or `Grep` for code search — use `rlm_search` or the REPL's `grep()` helper

### Navigation Tools

| Tool | Purpose |
|------|---------|
| `get_status` | Check daemon health |
| `rlm_tree` | See directory structure (replaces ls/find/Glob) |
| `rlm_map` | See file signatures only (replaces Read/cat) |
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

### Session End
- Before ending a session, call `get_status` to show the token savings summary

<!-- rlm-navigator:end -->
