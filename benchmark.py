"""RLM Navigator Workflow Simulation Benchmark

Modes:
  workflow    — Traditional (grep + full reads) vs RLM (tree + map + drill)
  truncation  — Measures savings from 8000-char response truncation
  repl        — REPL-assisted targeted reads vs full-file reads
  chunks      — Full-file vs skeleton vs per-chunk analysis costs

Usage:
    python benchmark.py --root . --query "squeeze"
    python benchmark.py --root . --query "squeeze" --mode truncation
    python benchmark.py --root . --query "handle_request" --mode repl
    python benchmark.py --root . --file "daemon/rlm_daemon.py" --mode chunks
"""

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Token estimation (conservative: ~4 chars/token for code)
# ---------------------------------------------------------------------------

def count_tokens(text: str) -> int:
    """Rough token estimate. Real tokenizers give ~3.5-4 chars/token for code."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Daemon client
# ---------------------------------------------------------------------------

DAEMON_PORT = 9177

def query_daemon(req: dict) -> dict:
    s = socket.socket()
    s.settimeout(10)
    s.connect(("127.0.0.1", DAEMON_PORT))
    s.send(json.dumps(req).encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
        try:
            json.loads(data)
            break
        except json.JSONDecodeError:
            continue
    s.close()
    return json.loads(data)


def daemon_alive() -> bool:
    try:
        r = query_daemon({"action": "status"})
        return r.get("status") == "alive"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Traditional workflow simulation
# ---------------------------------------------------------------------------

def traditional_workflow(root: str, search_query: str) -> dict:
    """Simulate: grep for files matching query → read each fully."""
    steps = []
    total_tokens = 0

    # Step 1: Developer would grep/search for relevant files
    # Simulate by walking the tree and reading files that match
    matching_files = []
    root_path = Path(root)
    ignored = {".git", "node_modules", "__pycache__", ".venv", "venv",
               "dist", "build", ".next", ".pytest_cache", "research"}

    for item in root_path.rglob("*"):
        if item.is_dir():
            continue
        if any(part in ignored for part in item.relative_to(root_path).parts):
            continue
        if item.suffix not in (".py", ".ts", ".js", ".go", ".rs", ".java", ".c", ".cpp", ".h"):
            continue
        try:
            content = item.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if search_query.lower() in content.lower():
            matching_files.append((str(item.relative_to(root_path)).replace("\\", "/"), content))

    # Step 2: Developer reads each matching file fully
    for rel_path, content in matching_files:
        tokens = count_tokens(content)
        total_tokens += tokens
        steps.append({
            "action": f"Read full file: {rel_path}",
            "tokens": tokens,
            "lines": content.count("\n") + 1,
        })

    return {
        "approach": "Traditional (grep + read full files)",
        "steps": steps,
        "total_tokens": total_tokens,
        "files_read": len(matching_files),
    }


# ---------------------------------------------------------------------------
# RLM workflow simulation
# ---------------------------------------------------------------------------

def rlm_workflow(root: str, search_query: str, tree_path: str = "") -> dict:
    """Simulate: get_status → tree → search → map candidates → drill symbols."""
    steps = []
    total_tokens = 0

    # Step 1: Health check
    status = query_daemon({"action": "status"})
    status_text = json.dumps(status)
    t = count_tokens(status_text)
    total_tokens += t
    steps.append({"action": "get_status", "tokens": t})

    # Step 2: Get tree (overview) — scoped to tree_path if provided
    tree_result = query_daemon({"action": "tree", "path": tree_path, "max_depth": 2})
    tree_text = json.dumps(tree_result)
    t = count_tokens(tree_text)
    total_tokens += t
    tree_label = tree_path if tree_path else "root"
    steps.append({"action": f"rlm_tree({tree_label}, depth=2)", "tokens": t})

    # Step 3: Search for query across skeletons
    search_result = query_daemon({"action": "search", "query": search_query, "path": ""})
    search_text = json.dumps(search_result)
    t = count_tokens(search_text)
    total_tokens += t
    hits = search_result.get("results", [])
    steps.append({
        "action": f"rlm_search(\"{search_query}\") → {len(hits)} files",
        "tokens": t,
    })

    # Step 4: Map each matching file (skeleton only)
    mapped_symbols = []
    for hit in hits:
        squeeze_result = query_daemon({"action": "squeeze", "path": hit["path"]})
        skeleton = squeeze_result.get("skeleton", "")
        t = count_tokens(skeleton)
        total_tokens += t
        steps.append({
            "action": f"rlm_map({hit['path']})",
            "tokens": t,
        })
        # Identify symbols to drill — only those matching the query
        for match_line in hit.get("matches", []):
            # Extract potential symbol names from match lines
            for word in match_line.split():
                cleaned = word.strip("(),:;{}[]#/*\"'")
                if search_query.lower() in cleaned.lower() and len(cleaned) > 2:
                    mapped_symbols.append((hit["path"], cleaned))
                    break

    # Step 5: Drill into specific symbols (the surgical part)
    drilled = set()
    for file_path, symbol in mapped_symbols[:5]:  # Cap at 5 drills (realistic)
        key = f"{file_path}:{symbol}"
        if key in drilled:
            continue
        drilled.add(key)

        find_result = query_daemon({"action": "find", "path": file_path, "symbol": symbol})
        if "error" in find_result:
            continue

        start = find_result["start_line"]
        end = find_result["end_line"]
        # Read those lines
        abs_path = os.path.join(root, file_path.replace("/", os.sep))
        try:
            lines = open(abs_path, encoding="utf-8").readlines()
            drill_text = "".join(lines[start - 1:end])
            t = count_tokens(drill_text)
            total_tokens += t
            steps.append({
                "action": f"rlm_drill({file_path}, \"{symbol}\") L{start}-{end}",
                "tokens": t,
                "lines": end - start + 1,
            })
        except Exception:
            pass

    return {
        "approach": "RLM Navigator (tree → search → map → drill)",
        "steps": steps,
        "total_tokens": total_tokens,
        "files_mapped": len(hits),
        "symbols_drilled": len(drilled),
    }


# ---------------------------------------------------------------------------
# Truncation helper (mirrors server's truncateResponse)
# ---------------------------------------------------------------------------

MAX_RESPONSE_CHARS = 8000

def truncate(text: str, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    remaining = len(text) - max_chars
    tokens_est = round(remaining / 4)
    return text[:max_chars] + f"\n... (truncated, {remaining} more chars, ~{tokens_est} tokens)"


# ---------------------------------------------------------------------------
# Mode: truncation — measure savings from response truncation
# ---------------------------------------------------------------------------

def truncation_benchmark(root: str, query: str):
    root_path = Path(root)
    ignored = {".git", "node_modules", "__pycache__", ".venv", "venv",
               "dist", "build", ".next", ".pytest_cache", "research"}
    rows = []  # (tool_call, raw_chars, truncated_chars)

    # tree at root depth 4
    tree_result = query_daemon({"action": "tree", "path": "", "max_depth": 4})
    raw = json.dumps(tree_result)
    trunc = truncate(raw)
    rows.append(("rlm_tree(root, depth=4)", len(raw), len(trunc)))

    # squeeze every supported file
    for item in root_path.rglob("*"):
        if item.is_dir():
            continue
        if any(part in ignored for part in item.relative_to(root_path).parts):
            continue
        if item.suffix not in (".py", ".ts", ".js", ".go", ".rs", ".java", ".c", ".cpp", ".h"):
            continue
        rel = str(item.relative_to(root_path)).replace("\\", "/")
        try:
            result = query_daemon({"action": "squeeze", "path": rel})
            raw = json.dumps(result)
            trunc = truncate(raw)
            rows.append((f"rlm_map({rel})", len(raw), len(trunc)))
        except Exception:
            pass

    # search
    search_result = query_daemon({"action": "search", "query": query, "path": ""})
    raw = json.dumps(search_result)
    trunc = truncate(raw)
    rows.append((f"rlm_search(\"{query}\")", len(raw), len(trunc)))

    print_truncation_report(rows, query)


def print_truncation_report(rows, query):
    width = 72
    print("=" * width)
    print("  RLM NAVIGATOR — TRUNCATION SAVINGS BENCHMARK")
    print("=" * width)
    print(f"  Query: \"{query}\"")
    print(f"  Truncation cap: {MAX_RESPONSE_CHARS:,} chars")
    print()

    total_raw = 0
    total_trunc = 0
    truncated_count = 0

    print(f"  {'Tool Call':<40} {'Raw':>8} {'Delivered':>10} {'Saved':>8}")
    print(f"  {'-'*40} {'-'*8} {'-'*10} {'-'*8}")
    for tool_call, raw_chars, trunc_chars in rows:
        saved = raw_chars - trunc_chars
        total_raw += raw_chars
        total_trunc += trunc_chars
        if saved > 0:
            truncated_count += 1
        label = tool_call if len(tool_call) <= 40 else tool_call[:37] + "..."
        print(f"  {label:<40} {raw_chars:>7}c {trunc_chars:>9}c {saved:>7}c")

    total_saved = total_raw - total_trunc
    pct = (total_saved / total_raw * 100) if total_raw > 0 else 0

    print(f"  {'-'*40} {'-'*8} {'-'*10} {'-'*8}")
    print(f"  {'TOTAL':<40} {total_raw:>7}c {total_trunc:>9}c {total_saved:>7}c")
    print()

    raw_tokens = count_tokens("x" * total_raw)
    delivered_tokens = count_tokens("x" * total_trunc)

    print(f"  {'SUMMARY':^{width-4}}")
    print(f"  {'=' * (width-4)}")
    print(f"    Tool calls:        {len(rows)}")
    print(f"    Truncated:         {truncated_count} / {len(rows)}")
    print(f"    Raw tokens:        {raw_tokens:,}t")
    print(f"    Delivered tokens:  {delivered_tokens:,}t")
    print(f"    Tokens saved:      {raw_tokens - delivered_tokens:,}t ({pct:.0f}% reduction)")
    print()

    bar_width = 50
    raw_bar = "#" * bar_width
    del_len = max(1, int(bar_width * total_trunc / total_raw)) if total_raw > 0 else 1
    del_bar = "#" * del_len + " " * (bar_width - del_len)
    print(f"    Raw:       [{raw_bar}] {raw_tokens:,}t")
    print(f"    Delivered: [{del_bar}] {delivered_tokens:,}t")
    print()
    print("=" * width)


# ---------------------------------------------------------------------------
# Mode: repl — REPL-assisted targeted reads vs full-file reads
# ---------------------------------------------------------------------------

def repl_benchmark(root: str, query: str):
    # --- Traditional side: grep + read full files ---
    trad_steps = []
    trad_tokens = 0
    root_path = Path(root)
    ignored = {".git", "node_modules", "__pycache__", ".venv", "venv",
               "dist", "build", ".next", ".pytest_cache", "research"}

    matching_files = []
    for item in root_path.rglob("*"):
        if item.is_dir():
            continue
        if any(part in ignored for part in item.relative_to(root_path).parts):
            continue
        if item.suffix not in (".py", ".ts", ".js", ".go", ".rs", ".java", ".c", ".cpp", ".h"):
            continue
        try:
            content = item.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if query.lower() in content.lower():
            rel = str(item.relative_to(root_path)).replace("\\", "/")
            matching_files.append((rel, content))

    for rel_path, content in matching_files:
        t = count_tokens(content)
        trad_tokens += t
        trad_steps.append({"action": f"Read full: {rel_path}", "tokens": t,
                           "lines": content.count("\n") + 1})

    # --- REPL side: init → grep → peek windows ---
    repl_steps = []
    repl_tokens = 0

    # repl_init
    init_result = query_daemon({"action": "repl_init"})
    init_text = json.dumps(init_result)
    t = count_tokens(init_text)
    repl_tokens += t
    repl_steps.append({"action": "repl_init", "tokens": t})

    # repl_exec: grep(query)
    grep_code = f'grep("{query}")'
    grep_result = query_daemon({"action": "repl_exec", "code": grep_code})
    grep_text = json.dumps(grep_result)
    t = count_tokens(grep_text)
    repl_tokens += t
    grep_output = grep_result.get("output", "")
    repl_steps.append({"action": f"repl_exec: grep(\"{query}\")", "tokens": t})

    # Parse grep output for file:line hits
    hits = []  # (file, line_num)
    for line in grep_output.splitlines():
        if ":" in line:
            parts = line.split(":", 2)
            if len(parts) >= 2:
                try:
                    hits.append((parts[0].strip(), int(parts[1].strip())))
                except (ValueError, IndexError):
                    pass

    # Deduplicate by file, collect all line numbers per file
    file_lines = {}
    for fpath, lnum in hits:
        file_lines.setdefault(fpath, []).append(lnum)

    # peek windows around each hit (5 lines of context)
    context_lines = 5
    for fpath, line_nums in file_lines.items():
        for lnum in line_nums[:3]:  # cap at 3 peek calls per file
            start = max(1, lnum - context_lines)
            end = lnum + context_lines
            peek_code = f'peek("{fpath}", {start}, {end})'
            peek_result = query_daemon({"action": "repl_exec", "code": peek_code})
            peek_text = json.dumps(peek_result)
            t = count_tokens(peek_text)
            repl_tokens += t
            repl_steps.append({
                "action": f"peek({fpath}, {start}, {end})",
                "tokens": t,
                "lines": end - start + 1,
            })

    print_repl_report(trad_steps, trad_tokens, repl_steps, repl_tokens, query)


def print_repl_report(trad_steps, trad_tokens, repl_steps, repl_tokens, query):
    width = 72
    print("=" * width)
    print("  RLM NAVIGATOR — REPL vs FULL-READ BENCHMARK")
    print("=" * width)
    print(f"  Query: \"{query}\"")
    print()

    # Traditional
    print(f"  {'TRADITIONAL (full-file reads)':^{width-4}}")
    print(f"  {'-' * (width-4)}")
    for step in trad_steps:
        lines = step.get("lines", "")
        line_info = f" ({lines} lines)" if lines else ""
        print(f"    {step['action']:<50} {step['tokens']:>5}t{line_info}")
    print(f"  {'-' * (width-4)}")
    print(f"  {'Files read:':<20} {len(trad_steps)}")
    print(f"  {'Total tokens:':<20} {trad_tokens:,}")
    print()

    # REPL
    print(f"  {'REPL (grep + peek windows)':^{width-4}}")
    print(f"  {'-' * (width-4)}")
    for step in repl_steps:
        lines = step.get("lines", "")
        line_info = f" ({lines} lines)" if lines else ""
        print(f"    {step['action']:<50} {step['tokens']:>5}t{line_info}")
    print(f"  {'-' * (width-4)}")
    print(f"  {'Tool calls:':<20} {len(repl_steps)}")
    print(f"  {'Total tokens:':<20} {repl_tokens:,}")
    print()

    # Comparison
    saved = trad_tokens - repl_tokens
    pct = (saved / trad_tokens * 100) if trad_tokens > 0 else 0

    print(f"  {'COMPARISON':^{width-4}}")
    print(f"  {'=' * (width-4)}")
    print()

    bar_width = 50
    trad_bar = "#" * bar_width
    repl_len = max(1, int(bar_width * repl_tokens / trad_tokens)) if trad_tokens > 0 else 1
    repl_bar = "#" * repl_len + " " * (bar_width - repl_len)

    print(f"    Traditional: [{trad_bar}] {trad_tokens:,}t")
    print(f"    REPL:        [{repl_bar}] {repl_tokens:,}t")
    print()
    print(f"    Tokens saved:  {saved:,} ({pct:.0f}% reduction)")
    if repl_tokens > 0:
        print(f"    Efficiency:    {trad_tokens / repl_tokens:.1f}x less context consumed")
    print()
    print("=" * width)


# ---------------------------------------------------------------------------
# Mode: chunks — full-file vs skeleton vs per-chunk analysis
# ---------------------------------------------------------------------------

def chunks_benchmark(root: str, file_path: str):
    abs_path = os.path.join(root, file_path.replace("/", os.sep))
    try:
        full_content = open(abs_path, encoding="utf-8").read()
    except FileNotFoundError:
        print(f"ERROR: File not found: {abs_path}")
        sys.exit(1)

    full_tokens = count_tokens(full_content)
    full_lines = full_content.count("\n") + 1

    # Skeleton via squeeze
    squeeze_result = query_daemon({"action": "squeeze", "path": file_path})
    skeleton = squeeze_result.get("skeleton", "")
    skeleton_tokens = count_tokens(skeleton)

    # Chunk workflow via REPL
    query_daemon({"action": "repl_init"})

    ci_code = f'chunk_indices("{file_path}")'
    ci_result = query_daemon({"action": "repl_exec", "code": ci_code})
    ci_output = ci_result.get("output", "")

    # Parse chunk boundaries from output like [(1, 200), (181, 380), ...]
    chunks = []
    repl_overhead_tokens = count_tokens(json.dumps(ci_result))
    try:
        # output should be a repr of list of tuples
        parsed = eval(ci_output.strip())
        if isinstance(parsed, list):
            chunks = parsed
    except Exception:
        # Fallback: try to parse manually
        for m in re.finditer(r'\((\d+),\s*(\d+)\)', ci_output):
            chunks.append((int(m.group(1)), int(m.group(2))))

    chunk_rows = []  # (label, tokens, lines)
    total_chunk_tokens = repl_overhead_tokens

    for i, (start, end) in enumerate(chunks):
        peek_code = f'peek("{file_path}", {start}, {end})'
        peek_result = query_daemon({"action": "repl_exec", "code": peek_code})
        peek_text = json.dumps(peek_result)
        t = count_tokens(peek_text)
        total_chunk_tokens += t
        chunk_rows.append((f"chunk[{i}] L{start}-{end}", t, end - start + 1))

    print_chunks_report(file_path, full_tokens, full_lines,
                        skeleton_tokens, chunk_rows, total_chunk_tokens, len(chunks))


def print_chunks_report(file_path, full_tokens, full_lines,
                        skeleton_tokens, chunk_rows, total_chunk_tokens, num_chunks):
    width = 72
    print("=" * width)
    print("  RLM NAVIGATOR — CHUNK ANALYSIS BENCHMARK")
    print("=" * width)
    print(f"  File: {file_path}")
    print(f"  Lines: {full_lines}")
    print()

    # Approaches
    print(f"  {'APPROACH COMPARISON':^{width-4}}")
    print(f"  {'-' * (width-4)}")
    print(f"    {'Full-file read:':<35} {full_tokens:>6,}t  ({full_lines} lines)")
    print(f"    {'Skeleton only (rlm_map):':<35} {skeleton_tokens:>6,}t")
    print(f"    {'All chunks (peek windows):':<35} {total_chunk_tokens:>6,}t  ({num_chunks} chunks)")
    if num_chunks > 0:
        avg = total_chunk_tokens // num_chunks
        print(f"    {'Avg tokens per chunk:':<35} {avg:>6,}t")
    print()

    # Chunk breakdown
    if chunk_rows:
        print(f"  {'CHUNK BREAKDOWN':^{width-4}}")
        print(f"  {'-' * (width-4)}")
        for label, tokens, lines in chunk_rows:
            print(f"    {label:<35} {tokens:>6}t  ({lines} lines)")
        print()

    # Bar chart
    print(f"  {'VISUAL COMPARISON':^{width-4}}")
    print(f"  {'=' * (width-4)}")
    print()

    bar_width = 50
    full_bar = "#" * bar_width

    skel_len = max(1, int(bar_width * skeleton_tokens / full_tokens)) if full_tokens > 0 else 1
    skel_bar = "#" * skel_len + " " * (bar_width - skel_len)

    chunk_len = max(1, int(bar_width * total_chunk_tokens / full_tokens)) if full_tokens > 0 else 1
    chunk_bar = "#" * chunk_len + " " * (bar_width - chunk_len)

    # Single chunk cost
    single_chunk_tokens = chunk_rows[0][1] if chunk_rows else 0
    single_len = max(1, int(bar_width * single_chunk_tokens / full_tokens)) if full_tokens > 0 else 1
    single_bar = "#" * single_len + " " * (bar_width - single_len)

    print(f"    Full file:    [{full_bar}] {full_tokens:,}t")
    print(f"    All chunks:   [{chunk_bar}] {total_chunk_tokens:,}t")
    print(f"    Skeleton:     [{skel_bar}] {skeleton_tokens:,}t")
    print(f"    One chunk:    [{single_bar}] {single_chunk_tokens:,}t")
    print()

    skel_pct = ((full_tokens - skeleton_tokens) / full_tokens * 100) if full_tokens > 0 else 0
    single_pct = ((full_tokens - single_chunk_tokens) / full_tokens * 100) if full_tokens > 0 else 0
    print(f"    Skeleton saves {skel_pct:.0f}% vs full file")
    print(f"    Single chunk saves {single_pct:.0f}% vs full file")
    print()
    print("=" * width)


# ---------------------------------------------------------------------------
# Report (workflow mode)
# ---------------------------------------------------------------------------

def print_report(traditional: dict, rlm: dict, query: str):
    width = 72

    print("=" * width)
    print("  RLM NAVIGATOR — WORKFLOW SIMULATION BENCHMARK")
    print("=" * width)
    print(f"  Task: Find and understand \"{query}\"")
    print()

    # Traditional
    print(f"  {'TRADITIONAL APPROACH':^{width-4}}")
    print(f"  {'-' * (width-4)}")
    print(f"  Strategy: grep for \"{query}\" → read every matching file fully")
    print()
    for step in traditional["steps"]:
        tokens = step["tokens"]
        lines = step.get("lines", "")
        line_info = f" ({lines} lines)" if lines else ""
        print(f"    {step['action']:<50} {tokens:>5}t{line_info}")
    print(f"  {'-' * (width-4)}")
    print(f"  {'Files read:':<20} {traditional['files_read']}")
    print(f"  {'Total tokens:':<20} {traditional['total_tokens']:,}")
    print()

    # RLM
    print(f"  {'RLM NAVIGATOR APPROACH':^{width-4}}")
    print(f"  {'-' * (width-4)}")
    print(f"  Strategy: tree → search → map skeletons → drill only needed symbols")
    print()
    for step in rlm["steps"]:
        tokens = step["tokens"]
        lines = step.get("lines", "")
        line_info = f" ({lines} lines)" if lines else ""
        print(f"    {step['action']:<50} {tokens:>5}t{line_info}")
    print(f"  {'-' * (width-4)}")
    print(f"  {'Files mapped:':<20} {rlm['files_mapped']}")
    print(f"  {'Symbols drilled:':<20} {rlm['symbols_drilled']}")
    print(f"  {'Total tokens:':<20} {rlm['total_tokens']:,}")
    print()

    # Comparison
    trad_t = traditional["total_tokens"]
    rlm_t = rlm["total_tokens"]
    saved = trad_t - rlm_t
    pct = (saved / trad_t * 100) if trad_t > 0 else 0

    print(f"  {'COMPARISON':^{width-4}}")
    print(f"  {'=' * (width-4)}")
    print()

    bar_width = 50
    trad_bar = "#" * bar_width
    rlm_bar_len = max(1, int(bar_width * rlm_t / trad_t)) if trad_t > 0 else 1
    rlm_bar = "#" * rlm_bar_len + " " * (bar_width - rlm_bar_len)

    print(f"    Traditional: [{trad_bar}] {trad_t:,}t")
    print(f"    RLM:         [{rlm_bar}] {rlm_t:,}t")
    print()
    print(f"    Tokens saved:  {saved:,} ({pct:.0f}% reduction)")
    if rlm_t > 0:
        print(f"    Efficiency:    {trad_t / rlm_t:.1f}x less context consumed")
    print()
    print("=" * width)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RLM Navigator Benchmark")
    parser.add_argument("--root", default=".", help="Project root (daemon must be watching this)")
    parser.add_argument("--query", default="squeeze", help="Symbol/term to search for")
    parser.add_argument("--port", type=int, default=9177, help="Daemon port")
    parser.add_argument("--tree-path", default="", help="Subdirectory for rlm_tree (e.g. 'src/auth'). Default: repo root")
    parser.add_argument("--mode", default="workflow",
                        choices=["workflow", "truncation", "repl", "chunks"],
                        help="Benchmark mode (default: workflow)")
    parser.add_argument("--file", default="", help="File path relative to root (required for chunks mode)")
    args = parser.parse_args()

    global DAEMON_PORT
    DAEMON_PORT = args.port

    root = str(Path(args.root).resolve())

    if args.mode == "chunks" and not args.file:
        print("ERROR: --file is required for chunks mode.")
        sys.exit(1)

    if not daemon_alive():
        print("ERROR: RLM daemon is not running.")
        print(f"  Start it: python daemon/rlm_daemon.py --root {root}")
        sys.exit(1)

    print(f"Running benchmark against: {root}")
    print(f"Mode: {args.mode}")
    if args.mode != "chunks":
        print(f"Search query: \"{args.query}\"")
    if args.tree_path:
        print(f"Tree scoped to: {args.tree_path}")
    print()

    if args.mode == "workflow":
        trad = traditional_workflow(root, args.query)
        rlm = rlm_workflow(root, args.query, tree_path=args.tree_path)
        print_report(trad, rlm, args.query)
    elif args.mode == "truncation":
        truncation_benchmark(root, args.query)
    elif args.mode == "repl":
        repl_benchmark(root, args.query)
    elif args.mode == "chunks":
        chunks_benchmark(root, args.file)


if __name__ == "__main__":
    main()
