# RLM Navigator

Token-efficient codebase navigation for AI-assisted coding. Treats codebases as navigable hierarchical trees of AST skeletons — the AI sees structure first, drills into implementations only when needed.

## Problem

AI coding assistants waste context window on full file reads. A 500-line file consumes ~2000 tokens even when you only need to understand one function. This leads to:

- **Context Bloat**: Window fills with irrelevant code
- **Context Rot**: Important earlier context gets pushed out
- **Exploration Loops**: AI re-reads files it already saw

## Solution

RLM Navigator provides 10 MCP tools that enforce a surgical navigation workflow:

**Navigation tools:**

| Tool | Purpose |
|------|---------|
| `get_status` | Check daemon health |
| `rlm_tree` | See directory structure (replaces ls/find) |
| `rlm_map` | See file signatures only (replaces cat/read) |
| `rlm_drill` | Read specific symbol implementation |
| `rlm_search` | Find symbols across files |

**REPL tools** (stateful Python environment with persistence):

| Tool | Purpose |
|------|---------|
| `rlm_repl_init` | Initialize the stateful REPL |
| `rlm_repl_exec` | Execute Python code (variables persist across calls) |
| `rlm_repl_status` | Check variables, buffers, execution count |
| `rlm_repl_reset` | Clear all REPL state |
| `rlm_repl_export` | Export accumulated buffers |

Built-in REPL helpers: `peek()` (read lines), `grep()` (regex search), `chunk_indices()` / `write_chunks()` (file chunking), `add_buffer()` (accumulate findings).

The workflow: **tree → map → drill → edit**. For complex analysis: **init → exec with helpers → export buffers**. Each step loads only what's needed.

## Architecture

```
┌─────────────┐     TCP/JSON      ┌──────────────┐
│  MCP Server  │ ───────────────→ │  Python       │
│  (TypeScript) │ ←─────────────── │  Daemon       │
└──────┬───────┘                  └──────┬────────┘
       │                                  │
       │ stdio                  watchdog  │ tree-sitter
       │                                  │
┌──────┴───────┐                  ┌──────┴────────┐
│  Claude Code  │                 │  File System   │
│  (AI Client)  │                 │  + AST Cache   │
└──────────────┘                  └───────────────┘
```

## Quick Start

### Install

```bash
# Unix/Mac
./install.sh

# Windows
.\install.ps1
```

### Manual Setup

```bash
# 1. Install Python deps
pip install -r daemon/requirements.txt

# 2. Build MCP server
cd server && npm install && npm run build

# 3. Register with Claude Code
claude mcp add rlm-navigator -- node /path/to/server/build/index.js
```

### Usage

```bash
# Start the daemon (in a separate terminal)
python daemon/rlm_daemon.py --root /path/to/your/project

# Now use Claude Code — the rlm_* tools are available
# Or invoke the /rlm-navigator skill for guided workflow
```

## Supported Languages

Tree-sitter powered parsing for: **Python, JavaScript, TypeScript, Go, Rust, Java, C, C++**

Unsupported file types get a graceful fallback (first 20 lines + line count).

## Benchmarks

Benchmarked against [tiangolo/fastapi](https://github.com/tiangolo/fastapi) using `benchmark.py`. The traditional approach greps for matches then reads every matching file in full. RLM navigates surgically: tree the structure, search skeletons, map only relevant files, drill only needed symbols.

| Query | Approach | Files | Tokens | Reduction | Efficiency |
|---|---|---|---|---|---|
| `authenticate` | Traditional | 42 full reads | 47,131 | — | — |
| `authenticate` | RLM (full repo tree) | 9 maps | 19,109 | 59% | 2.5x |
| `authenticate` | RLM (targeted tree) | 9 maps | **8,364** | **82%** | **5.6x** |
| `OAuth2PasswordBearer` | Traditional | 20 full reads | 25,725 | — | — |
| `OAuth2PasswordBearer` | RLM (full repo tree) | 1 map | 14,012 | 46% | 1.8x |
| `OAuth2PasswordBearer` | RLM (targeted tree) | 1 map | **3,267** | **87%** | **7.9x** |

Scoping `rlm_tree` to the relevant subdirectory (`--tree-path fastapi/security`) is critical for large repos — it reduces tree overhead from ~11K tokens to 205, making the difference between 2-3x and **6-8x** savings.

```bash
# Run the benchmark yourself
python benchmark.py --root /path/to/project --query "your_symbol" --tree-path "src/relevant/dir"
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `RLM_DAEMON_PORT` | `9177` | TCP port for daemon communication |
| `RLM_MAX_RESPONSE` | `8000` | Max chars before output truncation |

## Development

```bash
# Run tests
cd daemon && python -m pytest tests/ -v

# Start daemon in dev mode
python daemon/rlm_daemon.py --root .
```

## How It Works

1. **Daemon** watches your project with `watchdog`, parses files with `tree-sitter`, caches AST skeletons
2. **REPL** provides a pickle-persisted Python environment with codebase helpers (peek, grep, chunking, buffers)
3. **MCP Server** bridges Claude Code to the daemon via TCP JSON protocol, with automatic output truncation
4. **Skill** enforces the navigation workflow (tree → map → drill → edit) and the chunk-delegate-synthesize workflow for large analyses
5. **Sub-agent** (Haiku) analyzes file chunks with structured output — relevance rankings, missing items, and suggested next queries

## Inspired By

- [brainqub3/claude_code_RLM](https://github.com/brainqub3/claude_code_RLM) — RLM for document navigation
- Tree-sitter — universal AST parsing
