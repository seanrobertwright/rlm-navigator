# RLM Navigator — Recursive Codebase Navigation Skill

## Purpose
Navigate codebases with minimal token usage by treating source files as hierarchical trees of AST skeletons. Never read full files when signatures suffice.

## Workflow — ALWAYS follow this order:

### 1. Health Check
```
get_status
```
If offline, tell the user to start the daemon:
```
python daemon/rlm_daemon.py --root <project_path>
```

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
