# RLM Navigator — Project Instructions

## What This Is
A token-efficient codebase navigation system. Instead of reading entire files, the AI navigates structural signatures (AST skeletons) and surgically drills into only the code it needs.

## Architecture
- **Daemon** (`daemon/`): Python process that watches files, caches AST skeletons, serves TCP queries
- **MCP Server** (`server/`): TypeScript bridge exposing daemon capabilities as MCP tools
- **Skill** (`.claude/skills/rlm-navigator/`): Enforces the recursive navigation workflow
- **Sub-agent** (`.claude/agents/rlm-subcall.md`): Haiku agent for batch skeleton analysis

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
| `daemon/rlm_daemon.py` | File watcher + TCP server + cache |
| `server/src/index.ts` | MCP server with 5 tools |
| `.claude/skills/rlm-navigator/SKILL.md` | Navigation workflow enforcement |
| `.claude/agents/rlm-subcall.md` | Haiku sub-agent for skeleton analysis |

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
# Squeezer tests
cd daemon && python -m pytest tests/ -v

# Manual daemon test
python rlm_daemon.py --root . &
python -c "import socket, json; s=socket.socket(); s.connect(('127.0.0.1', 9177)); s.send(json.dumps({'action':'status'}).encode()); print(s.recv(4096).decode()); s.close()"
```
