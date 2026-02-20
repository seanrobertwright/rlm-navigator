"""RLM Navigator Workflow Simulation Benchmark

Simulates a realistic coding task two ways:
  1. Traditional: grep for relevant files → read them fully
  2. RLM: tree → map candidates → drill only needed symbols

Compares total tokens consumed and prints a detailed report.

Usage:
    python benchmark.py --root <project_path> --task "find the authentication logic"
    python benchmark.py --root <project_path> --query "authenticate"
"""

import argparse
import json
import os
import socket
import sys
import time
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
# Report
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
    args = parser.parse_args()

    global DAEMON_PORT
    DAEMON_PORT = args.port

    root = str(Path(args.root).resolve())

    if not daemon_alive():
        print("ERROR: RLM daemon is not running.")
        print(f"  Start it: python daemon/rlm_daemon.py --root {root}")
        sys.exit(1)

    print(f"Running benchmark against: {root}")
    print(f"Search query: \"{args.query}\"")
    if args.tree_path:
        print(f"Tree scoped to: {args.tree_path}")
    print()

    trad = traditional_workflow(root, args.query)
    rlm = rlm_workflow(root, args.query, tree_path=args.tree_path)
    print_report(trad, rlm, args.query)


if __name__ == "__main__":
    main()
