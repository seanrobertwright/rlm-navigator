# RLM Navigator — Project Instructions

## What This Is
A token-efficient codebase navigation system. Instead of reading entire files, the AI navigates structural signatures (AST skeletons) and surgically drills into only the code it needs.

## Architecture
- **Daemon** (`daemon/`): Python process that watches files, caches AST skeletons, serves TCP queries
- **MCP Server** (`server/`): TypeScript bridge exposing daemon capabilities as MCP tools
- **Skill** (`.claude/skills/rlm-navigator/`): Enforces the recursive navigation workflow
- **Sub-agent** (`.claude/agents/rlm-subcall.md`): Haiku agent for chunk analysis with structured output

## Token Conservation Rules

1. **Never read full files** — use `rlm_map` for signatures, `rlm_drill` for specific symbols
2. **Never use ls/find/Glob** for exploration — use `rlm_tree`
3. **Map before drill** — always see the skeleton first
4. **Surgical edits only** — use Edit tool with minimal context
5. **Minimalist responses** — no verbose explanations unless asked
6. **No redundant reads** — if it's in context, don't read it again

## Key File Map

| File | Purpose |
|------|---------|
| `daemon/squeezer.py` | Multi-language AST parser (tree-sitter) |
| `daemon/rlm_daemon.py` | File watcher + TCP server + cache (port scanning 9177-9196) |
| `daemon/rlm_repl.py` | Stateful REPL with pickle persistence + helpers |
| `server/src/index.ts` | MCP server with 10 tools (port discovery via `.rlm/port`, auto-spawn) |
| `bin/cli.js` | CLI entry point (`npx rlm-navigator install/uninstall/status`) |
| `templates/CLAUDE_SNIPPET.md` | CLAUDE.md snippet injected during install |
| `.claude/skills/rlm-navigator/SKILL.md` | Navigation workflow enforcement |
| `.claude/agents/rlm-subcall.md` | Haiku sub-agent for chunk analysis |

When installed via `npx rlm-navigator install`, files live in `.rlm/` inside the project. The daemon writes its bound port to `.rlm/port`; the MCP server reads this file for port discovery.

## REPL Tools

The REPL provides a stateful Python environment persisted via pickle:

| MCP Tool | Description |
|----------|-------------|
| `rlm_repl_init` | Initialize fresh REPL state |
| `rlm_repl_exec` | Execute Python code (variables persist) |
| `rlm_repl_status` | Check variables, buffers, exec count |
| `rlm_repl_reset` | Clear all state |
| `rlm_repl_export` | Export accumulated buffers |

### Built-in Helpers (available in `rlm_repl_exec`)

| Helper | Signature | Purpose |
|--------|-----------|---------|
| `peek` | `peek(file_path, start=1, end=None)` | Read numbered lines from file |
| `grep` | `grep(pattern, path=".", max_results=50)` | Regex search across files |
| `chunk_indices` | `chunk_indices(file_path, size=200, overlap=20)` | Compute chunk boundaries |
| `write_chunks` | `write_chunks(file_path, out_dir=None, size=200, overlap=20)` | Write chunks to disk |
| `add_buffer` | `add_buffer(key, text)` | Accumulate findings in named buffers |

## Development

```bash
# Start daemon (watches current directory)
cd daemon && python rlm_daemon.py --root /path/to/project

# Build MCP server
cd server && npm install && npm run build

# Register with Claude Code
claude mcp add rlm-navigator -- node /absolute/path/to/server/build/index.js
```

## Testing

```bash
# All daemon tests (squeezer + daemon + REPL)
cd daemon && python -m pytest tests/ -v

# REPL tests only
cd daemon && python -m pytest tests/test_repl.py -v

# Manual daemon test
python rlm_daemon.py --root . &
python -c "import socket, json; s=socket.socket(); s.connect(('127.0.0.1', 9177)); s.send(json.dumps({'action':'status'}).encode()); print(s.recv(4096).decode()); s.close()"
```
