#!/usr/bin/env python3
"""RLM Navigator --Comprehensive Token Savings Benchmark

Generates a detailed report demonstrating AND challenging all savings claims.
No running daemon required -tests core squeeze/find_symbol directly.

Usage:
    python benchmarks/run_benchmark.py                          # Full report
    python benchmarks/run_benchmark.py --json                   # Also emit JSON
    python benchmarks/run_benchmark.py --test skeleton          # Single test
    python benchmarks/run_benchmark.py --repo cpython           # Against CPython
    python benchmarks/run_benchmark.py --repo vscode            # Against VS Code
    python benchmarks/run_benchmark.py --repo /path/to/repo     # Local repo
    python benchmarks/run_benchmark.py --test repo --repo cpython  # Repo test only

Available tests:
    skeleton   - rlm_map vs full file Read (by size & language)
    drill      - rlm_drill vs full file Read (surgical extraction)
    search     - rlm_search vs grep (FAIR comparison)
    tree       - rlm_tree vs ls/Glob (directory exploration)
    doc        - rlm_doc_map/drill vs full Read (document navigation)
    chunks     - chunk read vs full file (large file handling)
    scaling    - savings curve across file sizes (breakeven analysis)
    tokens     - bytes/4 estimation accuracy analysis
    e2e        - end-to-end workflow simulation
    ai         - AI features (enrichment, MCTS, agents)
    repo       - real repo benchmark (requires --repo flag)

Live AI enrichment (requires auth):
    python benchmarks/run_benchmark.py --test ai --token <TOKEN>   # With explicit token
    # Or: start daemon via Claude Code MCP server, then run benchmark.
    # The benchmark auto-detects running daemons with enrichment enabled.

Known repos (--repo shortnames):
    cpython, vscode, kubernetes, django, fastapi, react,
    linux, rust, transformers, flask
"""

import argparse
import json
import math
import os
import re
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

# Add daemon/ to path so we can import squeezer directly
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "daemon"))

from squeezer import squeeze, find_symbol, supported_languages

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def count_tokens_bytes4(text: str) -> int:
    """Rough token estimate: bytes / 4. Used by daemon."""
    return max(1, len(text.encode("utf-8")) // 4)


def count_tokens_chars4(text: str) -> int:
    """Character-based estimate: chars / 4."""
    return max(1, len(text) // 4)


def count_tokens_word_approx(text: str) -> int:
    """Word-based estimate: ~1.3 tokens per word (code average)."""
    words = len(text.split())
    return max(1, int(words * 1.3))


# ---------------------------------------------------------------------------
# Daemon communication helpers (for live enrichment via Claude Code auth)
# ---------------------------------------------------------------------------

import socket as _socket


def _find_daemon_port() -> Optional[int]:
    """Find a running daemon with enrichment enabled.

    Checks .rlm/port first, then scans the port range.
    Only returns a port if the daemon has enrichment_available=True.
    """
    def _check_port(port):
        try:
            s = _socket.socket()
            s.settimeout(2)
            s.connect(("127.0.0.1", port))
            s.send(json.dumps({"action": "status"}).encode())
            data = s.recv(4096)
            s.close()
            resp = json.loads(data)
            if resp.get("status") == "alive" and resp.get("enrichment_available"):
                return True
            return False
        except (ConnectionRefusedError, OSError, json.JSONDecodeError):
            try:
                s.close()
            except Exception:
                pass
            return False

    # Check .rlm/port in project root
    port_file = PROJECT_ROOT / ".rlm" / "port"
    if port_file.exists():
        try:
            port = int(port_file.read_text().strip())
            if _check_port(port):
                return port
        except (ValueError, OSError):
            pass

    # Scan port range for any daemon with enrichment (quick check)
    for port in range(9177, 9197):
        try:
            s = _socket.socket()
            s.settimeout(0.3)
            s.connect(("127.0.0.1", port))
            s.close()
            # Port is open, do full check
            if _check_port(port):
                return port
        except (ConnectionRefusedError, OSError):
            try:
                s.close()
            except Exception:
                pass
    return None


def _start_daemon_with_token(auth_token: str) -> Optional[int]:
    """Start a daemon for THIS project with the given auth token.

    Returns the port it bound to, or None on failure.
    """
    import subprocess

    daemon_script = PROJECT_ROOT / "daemon" / "rlm_daemon.py"
    if not daemon_script.exists():
        return None

    env = os.environ.copy()
    env["ANTHROPIC_AUTH_TOKEN"] = auth_token

    # Start daemon in background
    try:
        proc = subprocess.Popen(
            [sys.executable, str(daemon_script), "--root", str(PROJECT_ROOT)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    # Wait for port file or scan
    port_file = PROJECT_ROOT / ".rlm" / "port"
    for _ in range(30):  # Wait up to 3 seconds
        time.sleep(0.1)
        if port_file.exists():
            try:
                port = int(port_file.read_text().strip())
                # Verify it's our daemon
                s = _socket.socket()
                s.settimeout(2)
                s.connect(("127.0.0.1", port))
                s.send(json.dumps({"action": "status"}).encode())
                data = s.recv(4096)
                s.close()
                resp = json.loads(data)
                if resp.get("status") == "alive":
                    return port
            except Exception:
                pass

    # Scan ports as fallback
    for port in range(9177, 9197):
        try:
            resp = _query_daemon(port, {"action": "status"}, timeout=2)
            if (resp.get("status") == "alive" and
                str(Path(resp.get("root", "")).resolve()) == str(PROJECT_ROOT.resolve())):
                return port
        except Exception:
            pass

    return None


def _query_daemon(port: int, req: dict, timeout: float = 30.0) -> dict:
    """Send a JSON request to the daemon and return parsed response."""
    s = _socket.socket()
    s.settimeout(timeout)
    s.connect(("127.0.0.1", port))
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


def _find_daemon_test_file(port: int) -> Optional[str]:
    """Find a suitable file in the daemon's project for enrichment testing.

    Picks a medium-sized Python file from the daemon's watched root.
    """
    try:
        result = _query_daemon(port, {"action": "tree", "path": "", "max_depth": 3}, timeout=5)
        tree = result.get("tree", {})

        # Walk the tree looking for .py files
        def _find_py_files(node, prefix=""):
            files = []
            if isinstance(node, dict):
                name = node.get("name", "")
                ntype = node.get("type", "")
                path = f"{prefix}/{name}".lstrip("/") if prefix else name

                if ntype == "file" and name.endswith(".py"):
                    size = node.get("size", 0)
                    if 500 < size < 20000:  # Medium-sized files
                        files.append((path, size))
                for child in node.get("children", []):
                    files.extend(_find_py_files(child, path))
            return files

        py_files = _find_py_files(tree)
        if py_files:
            # Pick one with reasonable size
            py_files.sort(key=lambda x: abs(x[1] - 5000))  # Closest to 5KB
            return py_files[0][0]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Sample code generators -realistic code at various sizes
# ---------------------------------------------------------------------------

def generate_python(num_functions: int = 5, lines_per_func: int = 10) -> str:
    """Generate realistic Python code with classes, methods, and standalone functions."""
    parts = [
        '"""Auto-generated sample module for benchmarking."""\n',
        "import os\nimport sys\nimport json\nfrom typing import Optional, List, Dict\n\n",
    ]

    # Generate a class with methods
    num_methods = max(2, num_functions // 2)
    parts.append("class DataProcessor:\n")
    parts.append('    """Processes data through multiple pipeline stages."""\n\n')
    parts.append("    def __init__(self, config: Dict):\n")
    parts.append('        """Initialize with configuration dict."""\n')
    parts.append("        self.config = config\n")
    parts.append("        self.pipeline = []\n")
    parts.append("        self.results = {}\n")
    parts.append("        self._initialized = False\n\n")

    for i in range(num_methods):
        parts.append(f"    def stage_{i}(self, data: List[Dict], threshold: float = 0.5) -> List[Dict]:\n")
        parts.append(f'        """Process stage {i}: filter and transform records."""\n')
        for j in range(lines_per_func):
            indent = "        "
            if j == 0:
                parts.append(f"{indent}filtered = []\n")
            elif j == lines_per_func - 1:
                parts.append(f"{indent}return filtered\n")
            elif j % 3 == 0:
                parts.append(f"{indent}for record in data:\n")
            elif j % 3 == 1:
                parts.append(f"{indent}    if record.get('score', 0) > threshold + {i * 0.1:.1f}:\n")
            else:
                parts.append(f"{indent}        filtered.append({{**record, 'stage': {i}}})\n")
        parts.append("\n")

    # Generate standalone functions
    num_standalone = max(2, num_functions - num_methods)
    for i in range(num_standalone):
        parts.append(f"def utility_func_{i}(input_data: str, verbose: bool = False) -> Optional[str]:\n")
        parts.append(f'    """Utility function {i}: validate and transform input."""\n')
        for j in range(lines_per_func):
            indent = "    "
            if j == 0:
                parts.append(f"{indent}if not input_data:\n")
            elif j == 1:
                parts.append(f"{indent}    return None\n")
            elif j == lines_per_func - 1:
                parts.append(f"{indent}return result\n")
            elif j % 4 == 0:
                parts.append(f"{indent}result = input_data.strip()\n")
            elif j % 4 == 1:
                parts.append(f"{indent}if verbose:\n")
            elif j % 4 == 2:
                parts.append(f'{indent}    print(f"Processing step {{j}} of func_{i}")\n')
            else:
                parts.append(f"{indent}result = result.replace('old_{j}', 'new_{j}')\n")
        parts.append("\n\n")

    # Add an async function
    parts.append("async def async_handler(request: Dict, timeout: float = 30.0) -> Dict:\n")
    parts.append('    """Handle async request with timeout and retry logic."""\n')
    for j in range(max(5, lines_per_func)):
        indent = "    "
        if j == 0:
            parts.append(f"{indent}import asyncio\n")
        elif j == 1:
            parts.append(f"{indent}try:\n")
        elif j == 2:
            parts.append(f'{indent}    result = await asyncio.wait_for(fetch(request), timeout=timeout)\n')
        elif j == 3:
            parts.append(f"{indent}except asyncio.TimeoutError:\n")
        elif j == 4:
            parts.append(f'{indent}    return {{"error": "timeout", "elapsed": timeout}}\n')
        elif j == max(5, lines_per_func) - 1:
            parts.append(f'{indent}return {{"status": "ok", "data": result}}\n')
        else:
            parts.append(f"{indent}result = transform_step_{j}(result)\n")

    return "".join(parts)


def generate_javascript(num_functions: int = 5, lines_per_func: int = 10) -> str:
    """Generate realistic JavaScript code."""
    parts = [
        "// Auto-generated sample module for benchmarking\n\n",
    ]

    parts.append("class UserService {\n")
    parts.append("  /**\n   * Manages user operations with database backing.\n   */\n")
    parts.append("  constructor(db, cache) {\n")
    parts.append("    this.db = db;\n    this.cache = cache;\n    this.logger = console;\n  }\n\n")

    num_methods = max(2, num_functions // 2)
    for i in range(num_methods):
        parts.append(f"  async method_{i}(userId, options = {{}}) {{\n")
        parts.append(f"    /** Process user operation {i}. */\n")
        for j in range(lines_per_func):
            indent = "    "
            if j == 0:
                parts.append(f"{indent}const cached = await this.cache.get(`user:${{userId}}`);\n")
            elif j == 1:
                parts.append(f"{indent}if (cached && !options.force) return cached;\n")
            elif j == lines_per_func - 1:
                parts.append(f"{indent}return result;\n")
            elif j % 3 == 0:
                parts.append(f"{indent}const step{j} = await this.db.query('SELECT * FROM users WHERE id = ?', [userId]);\n")
            elif j % 3 == 1:
                parts.append(f"{indent}if (!step{j-1}) throw new Error(`User ${{userId}} not found at step {j}`);\n")
            else:
                parts.append(f"{indent}const result = {{ ...step{j-2}, processed: true, stage: {i} }};\n")
        parts.append("  }\n\n")
    parts.append("}\n\n")

    num_standalone = max(2, num_functions - num_methods)
    for i in range(num_standalone):
        parts.append(f"function processData_{i}(items, config = {{}}) {{\n")
        parts.append(f"  /** Transform items according to config {i}. */\n")
        for j in range(lines_per_func):
            indent = "  "
            if j == 0:
                parts.append(f"{indent}const results = [];\n")
            elif j == lines_per_func - 1:
                parts.append(f"{indent}return results;\n")
            elif j % 2 == 0:
                parts.append(f"{indent}for (const item of items) {{\n")
            else:
                parts.append(f"{indent}  results.push({{ ...item, flag_{j}: true }});\n{indent}}}\n")
        parts.append("}\n\n")

    parts.append(f"const helperArrow = (x) => x * 2;\n\n")
    parts.append("module.exports = { UserService")
    for i in range(num_standalone):
        parts.append(f", processData_{i}")
    parts.append(" };\n")

    return "".join(parts)


def generate_typescript(num_functions: int = 5, lines_per_func: int = 10) -> str:
    """Generate realistic TypeScript code with interfaces and types."""
    parts = [
        "// Auto-generated TypeScript sample for benchmarking\n\n",
        "interface Config {\n  host: string;\n  port: number;\n  debug?: boolean;\n}\n\n",
        "type Result<T> = { success: true; data: T } | { success: false; error: string };\n\n",
    ]

    parts.append("export class ApiClient {\n")
    parts.append("  private config: Config;\n  private token: string | null = null;\n\n")
    parts.append("  constructor(config: Config) {\n    this.config = config;\n  }\n\n")

    num_methods = max(2, num_functions // 2)
    for i in range(num_methods):
        parts.append(f"  async request_{i}(endpoint: string, payload?: Record<string, unknown>): Promise<Result<unknown>> {{\n")
        parts.append(f"    /** Send request to endpoint {i}. */\n")
        for j in range(lines_per_func):
            indent = "    "
            if j == 0:
                parts.append(f"{indent}const url = `${{this.config.host}}:${{this.config.port}}/${{endpoint}}`;\n")
            elif j == lines_per_func - 1:
                parts.append(f"{indent}return {{ success: true, data: response }};\n")
            elif j % 3 == 0:
                parts.append(f"{indent}const response = await fetch(url, {{ method: 'POST', body: JSON.stringify(payload) }});\n")
            elif j % 3 == 1:
                parts.append(f"{indent}if (!response.ok) return {{ success: false, error: `HTTP ${{response.status}}` }};\n")
            else:
                parts.append(f"{indent}const data_{j} = await response.json();\n")
        parts.append("  }\n\n")
    parts.append("}\n\n")

    num_standalone = max(2, num_functions - num_methods)
    for i in range(num_standalone):
        parts.append(f"export function transform_{i}<T extends Record<string, unknown>>(items: T[], key: keyof T): T[] {{\n")
        parts.append(f"  /** Transform function {i}. */\n")
        for j in range(lines_per_func):
            indent = "  "
            if j == 0:
                parts.append(f"{indent}const mapped: T[] = [];\n")
            elif j == lines_per_func - 1:
                parts.append(f"{indent}return mapped;\n")
            else:
                parts.append(f"{indent}mapped.push({{ ...items[{j % 3}], [`transformed_${{key as string}}`]: true }} as T);\n")
        parts.append("}\n\n")

    return "".join(parts)


def generate_go(num_functions: int = 5, lines_per_func: int = 10) -> str:
    """Generate realistic Go code."""
    parts = [
        "package main\n\n",
        'import (\n\t"fmt"\n\t"errors"\n)\n\n',
    ]

    parts.append("// Service handles business logic.\ntype Service struct {\n")
    parts.append("\tdb     Database\n\tcache  Cache\n\tlogger Logger\n}\n\n")

    for i in range(num_functions):
        parts.append(f"// Process{i} handles operation {i} with validation.\n")
        parts.append(f"func (s *Service) Process{i}(input string, count int) (string, error) {{\n")
        for j in range(lines_per_func):
            indent = "\t"
            if j == 0:
                parts.append(f'{indent}if input == "" {{\n{indent}\treturn "", errors.New("empty input")\n{indent}}}\n')
            elif j == lines_per_func - 1:
                parts.append(f'{indent}return fmt.Sprintf("result_%d: %s", count, input), nil\n')
            else:
                parts.append(f'{indent}step{j} := fmt.Sprintf("step_%d_%d", {i}, {j})\n')
                parts.append(f'{indent}_ = step{j}\n')
        parts.append("}\n\n")

    return "".join(parts)


GENERATORS = {
    "python": (".py", generate_python),
    "javascript": (".js", generate_javascript),
    "typescript": (".ts", generate_typescript),
    "go": (".go", generate_go),
}

# File size presets: (num_functions, lines_per_func) -> approximate total lines
SIZE_PRESETS = {
    "tiny":   (2, 5),     # ~25 lines
    "small":  (3, 8),     # ~50 lines
    "medium": (5, 12),    # ~120 lines
    "large":  (8, 20),    # ~300 lines
    "xlarge": (15, 25),   # ~600 lines
    "huge":   (25, 35),   # ~1200 lines
}


def generate_sample_file(lang: str, size: str) -> tuple[str, str, str]:
    """Generate a sample file. Returns (extension, content, filename)."""
    if lang not in GENERATORS:
        raise ValueError(f"Unsupported language: {lang}")
    ext, gen_func = GENERATORS[lang]
    num_funcs, lines_per = SIZE_PRESETS[size]
    content = gen_func(num_funcs, lines_per)
    filename = f"sample_{size}{ext}"
    return ext, content, filename


# ---------------------------------------------------------------------------
# Markdown document generator
# ---------------------------------------------------------------------------

def generate_markdown(num_sections: int = 8, lines_per_section: int = 15) -> str:
    """Generate realistic markdown documentation."""
    parts = ["# API Documentation\n\n"]
    parts.append("This document describes the complete API surface.\n\n")

    for i in range(num_sections):
        parts.append(f"## Section {i}: Feature {chr(65 + i)}\n\n")
        parts.append(f"Feature {chr(65 + i)} provides functionality for handling operation {i}.\n\n")

        parts.append(f"### Configuration\n\n")
        parts.append("```json\n")
        parts.append(f'{{\n  "feature_{i}": {{\n    "enabled": true,\n    "threshold": {i * 0.1:.1f}\n  }}\n}}\n')
        parts.append("```\n\n")

        parts.append(f"### Usage\n\n")
        for j in range(lines_per_section):
            parts.append(f"Step {j+1}: Perform action {chr(65 + i)}-{j} with the configured parameters. "
                         f"This involves calling the appropriate handler and processing the result "
                         f"through the validation pipeline.\n\n")

        parts.append(f"### Notes\n\n")
        parts.append(f"- Important consideration for feature {chr(65 + i)}\n")
        parts.append(f"- Performance impact: O(n log n) for large datasets\n")
        parts.append(f"- See also: Section {(i + 1) % num_sections}\n\n")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Test implementations
# ---------------------------------------------------------------------------

class BenchmarkResults:
    """Collects and formats benchmark results."""

    def __init__(self):
        self.tests: list[dict] = []
        self.warnings: list[str] = []
        self.challenger_notes: list[str] = []

    def add_test(self, name: str, results: dict):
        self.tests.append({"name": name, **results})

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def add_challenger_note(self, msg: str):
        self.challenger_notes.append(msg)


def test_skeleton_by_size(tmpdir: Path) -> dict:
    """TEST 1: Skeleton extraction savings across file sizes."""
    rows = []

    for size_name, (nf, lpf) in SIZE_PRESETS.items():
        ext, content, filename = generate_sample_file("python", size_name)
        filepath = tmpdir / filename
        filepath.write_text(content, encoding="utf-8")

        skeleton = squeeze(str(filepath))
        full_bytes = len(content.encode("utf-8"))
        skel_bytes = len(skeleton.encode("utf-8"))
        full_lines = content.count("\n") + 1
        savings_pct = ((full_bytes - skel_bytes) / full_bytes * 100) if full_bytes > 0 else 0
        ratio = full_bytes / skel_bytes if skel_bytes > 0 else float("inf")

        rows.append({
            "size": size_name,
            "lines": full_lines,
            "full_bytes": full_bytes,
            "skeleton_bytes": skel_bytes,
            "savings_pct": round(savings_pct, 1),
            "ratio": round(ratio, 1),
            "full_tokens_est": count_tokens_bytes4(content),
            "skel_tokens_est": count_tokens_bytes4(skeleton),
        })

    # Find breakeven point (where savings > 30%)
    breakeven = None
    for row in rows:
        if row["savings_pct"] > 30:
            breakeven = row["lines"]
            break

    return {
        "rows": rows,
        "breakeven_lines": breakeven,
        "claim": "rlm_map shows signatures only, replacing full file reads",
        "verdict": "CONFIRMED" if any(r["savings_pct"] > 70 for r in rows) else "WEAK",
    }


def test_skeleton_by_language(tmpdir: Path) -> dict:
    """TEST 2: Skeleton extraction across languages."""
    rows = []
    size = "medium"  # ~120 lines, fair comparison

    for lang_name, (ext, gen_func) in GENERATORS.items():
        nf, lpf = SIZE_PRESETS[size]
        content = gen_func(nf, lpf)
        filename = f"sample_{lang_name}{ext}"
        filepath = tmpdir / filename
        filepath.write_text(content, encoding="utf-8")

        skeleton = squeeze(str(filepath))
        full_bytes = len(content.encode("utf-8"))
        skel_bytes = len(skeleton.encode("utf-8"))
        full_lines = content.count("\n") + 1

        # Check if skeleton is an error (unsupported language)
        is_error = skeleton.startswith("Error") or skeleton.startswith("unsupported")

        savings_pct = ((full_bytes - skel_bytes) / full_bytes * 100) if full_bytes > 0 and not is_error else 0
        ratio = full_bytes / skel_bytes if skel_bytes > 0 and not is_error else 0

        rows.append({
            "language": lang_name,
            "extension": ext,
            "lines": full_lines,
            "full_bytes": full_bytes,
            "skeleton_bytes": skel_bytes,
            "savings_pct": round(savings_pct, 1),
            "ratio": round(ratio, 1),
            "supported": not is_error,
        })

    supported = [r for r in rows if r["supported"]]
    avg_savings = sum(r["savings_pct"] for r in supported) / len(supported) if supported else 0

    return {
        "rows": rows,
        "avg_savings": round(avg_savings, 1),
        "supported_count": len(supported),
        "total_count": len(rows),
        "claim": "Multi-language AST extraction preserves signatures, strips bodies",
        "verdict": "CONFIRMED" if avg_savings > 50 else "PARTIAL",
    }


def test_drill_savings(tmpdir: Path) -> dict:
    """TEST 3: Surgical drill extracts only needed symbol lines."""
    rows = []

    for size_name in ["medium", "large", "xlarge"]:
        ext, content, filename = generate_sample_file("python", size_name)
        filepath = tmpdir / filename
        filepath.write_text(content, encoding="utf-8")

        full_bytes = len(content.encode("utf-8"))
        full_lines = content.count("\n") + 1

        # Try to find a symbol
        symbols_to_try = ["DataProcessor", "stage_0", "utility_func_0", "async_handler"]
        for sym in symbols_to_try:
            result = find_symbol(str(filepath), sym)
            if result:
                start, end = result
                lines = content.splitlines()
                drilled = "\n".join(lines[start - 1:end])
                drilled_bytes = len(drilled.encode("utf-8"))
                drilled_lines = end - start + 1

                savings_pct = ((full_bytes - drilled_bytes) / full_bytes * 100) if full_bytes > 0 else 0
                ratio = full_bytes / drilled_bytes if drilled_bytes > 0 else float("inf")

                rows.append({
                    "file_size": size_name,
                    "file_lines": full_lines,
                    "symbol": sym,
                    "drilled_lines": drilled_lines,
                    "full_bytes": full_bytes,
                    "drilled_bytes": drilled_bytes,
                    "savings_pct": round(savings_pct, 1),
                    "ratio": round(ratio, 1),
                })
                break

    avg_savings = sum(r["savings_pct"] for r in rows) / len(rows) if rows else 0

    return {
        "rows": rows,
        "avg_savings": round(avg_savings, 1),
        "claim": "rlm_drill reads ONLY the lines of the specific symbol",
        "verdict": "CONFIRMED" if avg_savings > 60 else "PARTIAL",
    }


def test_search_fair_comparison(tmpdir: Path) -> dict:
    """TEST 4: rlm_search vs grep -FAIR comparison.

    Challenger concern: daemon compares search output against FULL FILE SIZES,
    but grep also returns only matching lines. This test does a fair comparison.
    """
    # Create a multi-file project
    project_dir = tmpdir / "project"
    project_dir.mkdir()

    files = {
        "auth.py": textwrap.dedent("""\
            class AuthManager:
                def authenticate(self, token: str) -> bool:
                    \"\"\"Validate authentication token.\"\"\"
                    if not token:
                        return False
                    return self._check_token_db(token)

                def _check_token_db(self, token: str) -> bool:
                    import hashlib
                    hashed = hashlib.sha256(token.encode()).hexdigest()
                    return hashed in self.valid_tokens
            """),
        "middleware.py": textwrap.dedent("""\
            from auth import AuthManager

            class AuthMiddleware:
                def __init__(self):
                    self.auth = AuthManager()

                def process_request(self, request):
                    \"\"\"Process incoming request for authentication.\"\"\"
                    token = request.headers.get('Authorization')
                    if not self.auth.authenticate(token):
                        return Response(status=401)
                    return None

                def process_response(self, request, response):
                    return response
            """),
        "views.py": textwrap.dedent("""\
            from middleware import AuthMiddleware

            class UserView:
                def get_profile(self, user_id: int):
                    \"\"\"Get user profile by ID.\"\"\"
                    return {"id": user_id, "name": "Test"}

                def update_profile(self, user_id: int, data: dict):
                    \"\"\"Update user profile.\"\"\"
                    return {"updated": True}

                def delete_profile(self, user_id: int):
                    \"\"\"Delete user profile.\"\"\"
                    return {"deleted": True}
            """),
        "utils.py": textwrap.dedent("""\
            def format_response(data):
                \"\"\"Format API response.\"\"\"
                return {"status": "ok", "data": data}

            def validate_input(data, schema):
                \"\"\"Validate input against schema.\"\"\"
                for key in schema:
                    if key not in data:
                        raise ValueError(f"Missing key: {key}")
                return True
            """),
    }

    for fname, content in files.items():
        (project_dir / fname).write_text(content, encoding="utf-8")

    query = "authenticate"

    # --- DAEMON CLAIM: avoided = sum(full file sizes) - response size ---
    daemon_claim_avoided = 0
    total_file_sizes = 0
    matching_files = []

    for fname, content in files.items():
        if query.lower() in content.lower():
            matching_files.append(fname)
            total_file_sizes += len(content.encode("utf-8"))

    # rlm_search response: skeleton excerpts with matches
    search_response_lines = []
    for fname in matching_files:
        filepath = project_dir / fname
        skeleton = squeeze(str(filepath))
        for line in skeleton.splitlines():
            if query.lower() in line.lower():
                search_response_lines.append(f"{fname}: {line.strip()}")
    search_response = "\n".join(search_response_lines)
    search_bytes = len(search_response.encode("utf-8"))

    daemon_claim_avoided = total_file_sizes - search_bytes

    # --- FAIR COMPARISON: grep also returns only matching lines ---
    grep_lines = []
    for fname, content in files.items():
        for i, line in enumerate(content.splitlines(), 1):
            if query.lower() in line.lower():
                grep_lines.append(f"{fname}:{i}:{line.strip()}")
    grep_response = "\n".join(grep_lines)
    grep_bytes = len(grep_response.encode("utf-8"))

    # --- FULL READ baseline (what daemon claims we'd do) ---
    full_read_bytes = total_file_sizes

    return {
        "query": query,
        "matching_files": len(matching_files),
        "total_files": len(files),

        # Daemon's claimed savings (vs full file reads)
        "daemon_claim": {
            "search_response_bytes": search_bytes,
            "full_files_bytes": full_read_bytes,
            "claimed_avoided": daemon_claim_avoided,
            "claimed_savings_pct": round(daemon_claim_avoided / full_read_bytes * 100, 1) if full_read_bytes > 0 else 0,
        },

        # Fair comparison (vs grep output)
        "fair_comparison": {
            "rlm_search_bytes": search_bytes,
            "grep_output_bytes": grep_bytes,
            "difference_bytes": abs(search_bytes - grep_bytes),
            "rlm_search_tokens": count_tokens_bytes4(search_response),
            "grep_tokens": count_tokens_bytes4(grep_response),
        },

        "claim": "rlm_search finds symbols without reading full files",
        "challenger_note": (
            "Daemon claims avoided = full_file_sizes - response_size. "
            "But grep ALSO returns only matching lines. "
            f"Fair comparison: rlm_search={search_bytes}B vs grep={grep_bytes}B -"
            f"{'similar cost' if abs(search_bytes - grep_bytes) < 100 else 'rlm_search is ' + ('larger' if search_bytes > grep_bytes else 'smaller')}. "
            "The REAL advantage is structural filtering (skeleton matches vs raw string matches)."
        ),
        "verdict": "OVERSTATED -search savings are similar to grep, not vs full file reads",
    }


def test_tree_comparison(tmpdir: Path) -> dict:
    """TEST 5: Tree exploration comparison."""
    # Create nested directory structure
    project = tmpdir / "treetest"
    project.mkdir()

    dirs_and_files = {
        "src/auth/login.py": generate_python(3, 8),
        "src/auth/register.py": generate_python(2, 6),
        "src/auth/__init__.py": "",
        "src/api/routes.py": generate_python(4, 10),
        "src/api/middleware.py": generate_python(3, 8),
        "src/api/__init__.py": "",
        "src/models/user.py": generate_python(2, 6),
        "src/models/product.py": generate_python(3, 8),
        "src/models/__init__.py": "",
        "src/utils/helpers.py": generate_python(5, 5),
        "src/utils/__init__.py": "",
        "tests/test_auth.py": generate_python(3, 5),
        "tests/test_api.py": generate_python(3, 5),
        "tests/__init__.py": "",
        "config/settings.json": '{"debug": true}',
        "config/deploy.yaml": "env: production",
        "README.md": "# Project\n\nDescription here.",
    }

    for rel_path, content in dirs_and_files.items():
        full_path = project / rel_path.replace("/", os.sep)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    # Simulate ls -R output
    ls_lines = []
    for dirpath, dirnames, filenames in os.walk(str(project)):
        rel = os.path.relpath(dirpath, str(project))
        ls_lines.append(f"\n{rel}:")
        for f in sorted(filenames):
            fpath = os.path.join(dirpath, f)
            size = os.path.getsize(fpath)
            ls_lines.append(f"  {f:<30} {size:>6}B")
    ls_output = "\n".join(ls_lines)
    ls_bytes = len(ls_output.encode("utf-8"))

    # Simulate rlm_tree output (metadata: name, type, size, language)
    tree_lines = []
    for dirpath, dirnames, filenames in os.walk(str(project)):
        rel = os.path.relpath(dirpath, str(project))
        depth = rel.count(os.sep) if rel != "." else 0
        indent = "  " * depth
        dirname = os.path.basename(dirpath) if rel != "." else "."
        tree_lines.append(f"{indent}{dirname}/")
        for f in sorted(filenames):
            fpath = os.path.join(dirpath, f)
            size = os.path.getsize(fpath)
            tree_lines.append(f"{indent}  {f} ({size}B)")
    tree_output = "\n".join(tree_lines)
    tree_bytes = len(tree_output.encode("utf-8"))

    # Glob would just return file paths
    glob_lines = sorted(dirs_and_files.keys())
    glob_output = "\n".join(glob_lines)
    glob_bytes = len(glob_output.encode("utf-8"))

    return {
        "files": len(dirs_and_files),
        "directories": len(set(str(Path(p).parent) for p in dirs_and_files)),
        "ls_bytes": ls_bytes,
        "tree_bytes": tree_bytes,
        "glob_bytes": glob_bytes,
        "ls_tokens": count_tokens_bytes4(ls_output),
        "tree_tokens": count_tokens_bytes4(tree_output),
        "glob_tokens": count_tokens_bytes4(glob_output),
        "claim": "rlm_tree replaces ls/find/Glob for directory exploration",
        "challenger_note": (
            f"Tree ({tree_bytes}B) vs ls -R ({ls_bytes}B) vs Glob ({glob_bytes}B). "
            "All three return metadata only -no file content. "
            "Token cost is comparable. The advantage is structural format, not token savings."
        ),
        "verdict": "COMPARABLE -tree provides better structure but similar token cost",
    }


def test_doc_navigation(tmpdir: Path) -> dict:
    """TEST 6: Document navigation savings."""
    doc_content = generate_markdown(8, 15)
    doc_file = tmpdir / "docs.md"
    doc_file.write_text(doc_content, encoding="utf-8")

    full_bytes = len(doc_content.encode("utf-8"))
    full_lines = doc_content.count("\n") + 1

    # Simulate doc_map: extract section headers only
    headers = []
    for i, line in enumerate(doc_content.splitlines(), 1):
        if line.startswith("#"):
            headers.append(f"L{i}: {line.strip()}")
    doc_map_output = "\n".join(headers)
    map_bytes = len(doc_map_output.encode("utf-8"))

    # Simulate doc_drill: extract one section
    # Find "Section 3" content
    lines = doc_content.splitlines()
    section_start = None
    section_end = None
    for i, line in enumerate(lines):
        if "## Section 3" in line:
            section_start = i
        elif section_start is not None and line.startswith("## ") and i > section_start:
            section_end = i
            break
    if section_start is not None:
        if section_end is None:
            section_end = len(lines)
        section_content = "\n".join(lines[section_start:section_end])
        drill_bytes = len(section_content.encode("utf-8"))
        drill_lines = section_end - section_start
    else:
        drill_bytes = 0
        drill_lines = 0

    # Combined: map + one drill vs full read
    combined_bytes = map_bytes + drill_bytes
    savings_pct = ((full_bytes - combined_bytes) / full_bytes * 100) if full_bytes > 0 else 0

    return {
        "full_bytes": full_bytes,
        "full_lines": full_lines,
        "map_bytes": map_bytes,
        "map_headers": len(headers),
        "drill_bytes": drill_bytes,
        "drill_lines": drill_lines,
        "combined_bytes": combined_bytes,
        "savings_pct": round(savings_pct, 1),
        "ratio": round(full_bytes / combined_bytes, 1) if combined_bytes > 0 else 0,
        "claim": "rlm_doc_map + rlm_doc_drill reads sections, not full documents",
        "verdict": "CONFIRMED" if savings_pct > 50 else "PARTIAL",
    }


def test_chunk_savings(tmpdir: Path) -> dict:
    """TEST 7: Chunk read vs full file."""
    ext, content, filename = generate_sample_file("python", "huge")
    filepath = tmpdir / filename
    filepath.write_text(content, encoding="utf-8")

    full_bytes = len(content.encode("utf-8"))
    full_lines = content.count("\n") + 1

    # Simulate chunking (200 lines, 20 overlap)
    chunk_size = 200
    overlap = 20
    lines = content.splitlines()
    chunks = []
    start = 0
    while start < len(lines):
        end = min(start + chunk_size, len(lines))
        chunk_content = "\n".join(lines[start:end])
        chunks.append({
            "start": start + 1,
            "end": end,
            "bytes": len(chunk_content.encode("utf-8")),
            "lines": end - start,
        })
        start += chunk_size - overlap
        if start >= len(lines):
            break

    single_chunk_bytes = chunks[0]["bytes"] if chunks else 0
    all_chunks_bytes = sum(c["bytes"] for c in chunks)

    single_savings = ((full_bytes - single_chunk_bytes) / full_bytes * 100) if full_bytes > 0 else 0

    return {
        "full_bytes": full_bytes,
        "full_lines": full_lines,
        "num_chunks": len(chunks),
        "chunk_size": chunk_size,
        "overlap": overlap,
        "single_chunk_bytes": single_chunk_bytes,
        "single_chunk_savings_pct": round(single_savings, 1),
        "all_chunks_bytes": all_chunks_bytes,
        "overhead_ratio": round(all_chunks_bytes / full_bytes, 2) if full_bytes > 0 else 0,
        "claim": "Reading one chunk avoids loading the full file",
        "challenger_note": (
            f"Single chunk ({single_chunk_bytes}B) vs full file ({full_bytes}B) = "
            f"{single_savings:.0f}% savings per chunk read. "
            f"BUT reading ALL chunks costs {all_chunks_bytes}B "
            f"({round(all_chunks_bytes / full_bytes * 100)}% of full) due to {overlap}-line overlap."
        ),
        "verdict": "CONFIRMED for targeted analysis; overhead if reading all chunks",
    }


def test_scaling_analysis(tmpdir: Path) -> dict:
    """TEST 8: Savings curve across file sizes -find breakeven point."""
    data_points = []

    # Test with fine-grained sizes
    configs = [
        (1, 3),   # ~10 lines
        (1, 5),   # ~15 lines
        (2, 5),   # ~25 lines
        (2, 8),   # ~35 lines
        (3, 8),   # ~50 lines
        (4, 10),  # ~80 lines
        (5, 12),  # ~120 lines
        (6, 15),  # ~170 lines
        (8, 20),  # ~300 lines
        (12, 25), # ~500 lines
        (20, 30), # ~900 lines
        (30, 35), # ~1500 lines
    ]

    for nf, lpf in configs:
        content = generate_python(nf, lpf)
        filename = f"scale_{nf}_{lpf}.py"
        filepath = tmpdir / filename
        filepath.write_text(content, encoding="utf-8")

        skeleton = squeeze(str(filepath))
        full_bytes = len(content.encode("utf-8"))
        skel_bytes = len(skeleton.encode("utf-8"))
        lines = content.count("\n") + 1

        # JSON overhead estimate (wrapping skeleton in daemon response)
        json_overhead = 80  # {"skeleton": "...", "path": "...", "symbols": N}
        effective_skel_bytes = skel_bytes + json_overhead

        savings_pct = ((full_bytes - skel_bytes) / full_bytes * 100) if full_bytes > 0 else 0
        effective_savings = ((full_bytes - effective_skel_bytes) / full_bytes * 100) if full_bytes > 0 else 0

        data_points.append({
            "lines": lines,
            "full_bytes": full_bytes,
            "skeleton_bytes": skel_bytes,
            "effective_bytes": effective_skel_bytes,
            "raw_savings_pct": round(savings_pct, 1),
            "effective_savings_pct": round(max(0, effective_savings), 1),
        })

    # Find breakeven (where effective savings > 0)
    breakeven = None
    for dp in data_points:
        if dp["effective_savings_pct"] > 0:
            breakeven = dp["lines"]
            break

    # Find "worthwhile" point (>50% savings)
    worthwhile = None
    for dp in data_points:
        if dp["effective_savings_pct"] > 50:
            worthwhile = dp["lines"]
            break

    return {
        "data_points": data_points,
        "breakeven_lines": breakeven,
        "worthwhile_lines": worthwhile,
        "claim": "Skeleton extraction provides meaningful savings",
        "challenger_note": (
            f"Breakeven at ~{breakeven} lines (savings > 0% including JSON overhead). "
            f"Worthwhile at ~{worthwhile} lines (savings > 50%). "
            "The skill's 50-line threshold for 'just read directly' is reasonable."
        ),
        "verdict": "CONFIRMED with caveat: savings scale with file size",
    }


def test_token_estimation(tmpdir: Path) -> dict:
    """TEST 9: How accurate is bytes/4 as a token estimate?

    Challenger concern: bytes/4 can be off by 50-100% for code due to
    symbol density, whitespace, and multi-byte characters.
    """
    samples = {}

    # Generate different types of content
    samples["python_code"] = generate_python(5, 10)
    samples["javascript_code"] = generate_javascript(5, 10)
    samples["json_config"] = json.dumps({
        "database": {"host": "localhost", "port": 5432, "name": "mydb"},
        "cache": {"driver": "redis", "ttl": 3600},
        "logging": {"level": "INFO", "format": "%(asctime)s %(message)s"},
    }, indent=2)
    samples["markdown_prose"] = generate_markdown(3, 5)
    samples["skeleton_output"] = squeeze(str(tmpdir / "dummy"))  # will error but that's fine

    # Generate a real skeleton for comparison
    ext, content, filename = generate_sample_file("python", "medium")
    filepath = tmpdir / f"token_test{ext}"
    filepath.write_text(content, encoding="utf-8")
    samples["skeleton_output"] = squeeze(str(filepath))

    results = []
    for name, text in samples.items():
        if not text or text.startswith("Error"):
            continue

        bytes_count = len(text.encode("utf-8"))
        chars_count = len(text)
        words_count = len(text.split())

        # Different estimation methods
        est_bytes4 = count_tokens_bytes4(text)
        est_chars4 = count_tokens_chars4(text)
        est_words = count_tokens_word_approx(text)

        # Analyze character composition
        alpha_count = sum(1 for c in text if c.isalpha())
        symbol_count = sum(1 for c in text if c in "{}()[]<>:;,./\\|!@#$%^&*+-=~`\"'")
        space_count = sum(1 for c in text if c.isspace())

        symbol_ratio = symbol_count / chars_count if chars_count > 0 else 0

        results.append({
            "content_type": name,
            "bytes": bytes_count,
            "chars": chars_count,
            "words": words_count,
            "est_bytes4": est_bytes4,
            "est_chars4": est_chars4,
            "est_words13": est_words,
            "symbol_density": round(symbol_ratio * 100, 1),
            "bytes_per_char": round(bytes_count / chars_count, 2) if chars_count > 0 else 0,
        })

    return {
        "results": results,
        "claim": "Token estimation uses bytes / 4",
        "challenger_note": (
            "bytes/4 is a rough estimate. For ASCII code (bytes ~= chars), "
            "it's equivalent to chars/4. Real tokenizers produce ~2.5-4.5 chars/token "
            "depending on content type. Code with high symbol density (braces, operators) "
            "tokenizes to MORE tokens than bytes/4 suggests. "
            "HOWEVER: since both baseline and RLM use the same estimator, "
            "the PERCENTAGE savings remain valid even if absolute counts are off."
        ),
        "verdict": "ACKNOWLEDGED -percentages valid, absolute counts approximate (+/-30-50%)",
    }


def test_end_to_end_workflow(tmpdir: Path) -> dict:
    """TEST 10: Full workflow comparison -traditional vs RLM.

    This test uses local files only (no daemon required).
    """
    # Create a realistic multi-file project
    project = tmpdir / "e2e_project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "auth").mkdir()
    (project / "src" / "api").mkdir()
    (project / "src" / "models").mkdir()
    (project / "tests").mkdir()

    files = {
        "src/auth/token_manager.py": generate_python(6, 15),
        "src/auth/permissions.py": generate_python(4, 12),
        "src/auth/__init__.py": "from .token_manager import *\nfrom .permissions import *\n",
        "src/api/routes.py": generate_python(8, 18),
        "src/api/middleware.py": generate_python(5, 12),
        "src/api/__init__.py": "",
        "src/models/user.py": generate_python(3, 10),
        "src/models/product.py": generate_python(4, 12),
        "src/models/__init__.py": "",
        "tests/test_auth.py": generate_python(5, 8),
        "tests/test_api.py": generate_python(6, 8),
    }

    for rel_path, content in files.items():
        full_path = project / rel_path.replace("/", os.sep)
        full_path.write_text(content, encoding="utf-8")

    query = "stage_0"  # A method that exists in generated code

    # --- TRADITIONAL APPROACH ---
    # Step 1: grep for query across all files
    trad_steps = []
    trad_tokens = 0

    matching_files = []
    for rel_path, content in files.items():
        if query.lower() in content.lower():
            matching_files.append((rel_path, content))

    # grep output
    grep_lines = []
    for rel_path, content in matching_files:
        for i, line in enumerate(content.splitlines(), 1):
            if query.lower() in line.lower():
                grep_lines.append(f"{rel_path}:{i}:{line.strip()}")
    grep_output = "\n".join(grep_lines)
    grep_tokens = count_tokens_bytes4(grep_output)
    trad_tokens += grep_tokens
    trad_steps.append({"action": f"grep '{query}' across project", "tokens": grep_tokens})

    # Step 2: Read each matching file fully
    for rel_path, content in matching_files:
        t = count_tokens_bytes4(content)
        trad_tokens += t
        trad_steps.append({"action": f"Read full: {rel_path}", "tokens": t,
                          "lines": content.count("\n") + 1})

    # --- RLM APPROACH ---
    rlm_steps = []
    rlm_tokens = 0

    # Step 1: Tree (directory overview)
    tree_lines = []
    for dirpath, dirnames, filenames in os.walk(str(project)):
        rel = os.path.relpath(dirpath, str(project))
        depth = rel.count(os.sep) if rel != "." else 0
        if depth > 2:
            continue
        indent = "  " * depth
        dirname = os.path.basename(dirpath) if rel != "." else "."
        tree_lines.append(f"{indent}{dirname}/")
        for f in sorted(filenames):
            fpath = os.path.join(dirpath, f)
            size = os.path.getsize(fpath)
            tree_lines.append(f"{indent}  {f} ({size}B)")
    tree_output = "\n".join(tree_lines)
    tree_tokens = count_tokens_bytes4(tree_output)
    rlm_tokens += tree_tokens
    rlm_steps.append({"action": "rlm_tree(depth=2)", "tokens": tree_tokens})

    # Step 2: Search for symbol
    search_lines = []
    for rel_path, content in files.items():
        filepath = project / rel_path.replace("/", os.sep)
        skeleton = squeeze(str(filepath))
        if query.lower() in skeleton.lower():
            for line in skeleton.splitlines():
                if query.lower() in line.lower():
                    search_lines.append(f"{rel_path}: {line.strip()}")
    search_output = "\n".join(search_lines)
    search_tokens = count_tokens_bytes4(search_output)
    rlm_tokens += search_tokens
    rlm_steps.append({"action": f"rlm_search('{query}')", "tokens": search_tokens})

    # Step 3: Map matching files (skeletons only)
    skeleton_files = []
    for rel_path, content in files.items():
        filepath = project / rel_path.replace("/", os.sep)
        skeleton = squeeze(str(filepath))
        if query.lower() in skeleton.lower():
            skel_tokens = count_tokens_bytes4(skeleton)
            rlm_tokens += skel_tokens
            rlm_steps.append({"action": f"rlm_map({rel_path})", "tokens": skel_tokens})
            skeleton_files.append((rel_path, content, filepath))

    # Step 4: Drill into the specific symbol
    for rel_path, content, filepath in skeleton_files[:3]:  # Cap at 3
        result = find_symbol(str(filepath), query)
        if result:
            start, end = result
            lines = content.splitlines()
            drilled = "\n".join(lines[start - 1:end])
            drill_tokens = count_tokens_bytes4(drilled)
            rlm_tokens += drill_tokens
            rlm_steps.append({
                "action": f"rlm_drill({rel_path}, '{query}') L{start}-{end}",
                "tokens": drill_tokens,
                "lines": end - start + 1,
            })

    saved = trad_tokens - rlm_tokens
    pct = (saved / trad_tokens * 100) if trad_tokens > 0 else 0

    return {
        "query": query,
        "total_files": len(files),
        "matching_files": len(matching_files),
        "traditional": {
            "steps": trad_steps,
            "total_tokens": trad_tokens,
        },
        "rlm": {
            "steps": rlm_steps,
            "total_tokens": rlm_tokens,
            "files_mapped": len(skeleton_files),
        },
        "savings_tokens": saved,
        "savings_pct": round(pct, 1),
        "ratio": round(trad_tokens / rlm_tokens, 1) if rlm_tokens > 0 else 0,
        "claim": "RLM workflow (tree->search->map->drill) uses fewer tokens than grep+read",
        "verdict": "CONFIRMED" if pct > 40 else "PARTIAL",
    }


# ---------------------------------------------------------------------------
# AI feature benchmarks
# ---------------------------------------------------------------------------

def test_ai_features(tmpdir: Path) -> dict:
    """TEST 11: AI-powered features -- enrichment, MCTS, agents.

    Tests the AI subsystem without requiring live API keys by measuring:
    - Skeleton parsing for enrichment (symbol extraction quality)
    - Enrichment prompt overhead (how many tokens does enrichment add?)
    - Enriched vs plain skeleton size comparison
    - MCTS session state management
    - Agent prompt construction
    - Config/provider detection
    """
    from config import RLMConfig

    # --- Check which AI features are available ---
    config = RLMConfig(str(tmpdir))
    ai_status = {
        "enrichment_enabled": config.enrichment_enabled,
        "enrichment_provider": config.enrichment_provider,
        "enrichment_model": config.enrichment_model,
        "anthropic_available": config.anthropic_available,
        "doc_indexing_enabled": config.doc_indexing_enabled,
    }

    # --- TEST A: Symbol parsing from real skeletons ---
    from node_enricher import (
        parse_skeleton_symbols, build_enrichment_prompt,
        merge_enrichments, EnrichmentCache,
    )

    # Generate a variety of files and squeeze them
    parse_results = []
    for size_name in ["small", "medium", "large", "xlarge"]:
        ext, content, filename = generate_sample_file("python", size_name)
        filepath = tmpdir / filename
        filepath.write_text(content, encoding="utf-8")

        skeleton = squeeze(str(filepath))
        symbols = parse_skeleton_symbols(skeleton)
        full_lines = content.count("\n") + 1

        parse_results.append({
            "size": size_name,
            "file_lines": full_lines,
            "skeleton_lines": skeleton.count("\n") + 1,
            "symbols_found": len(symbols),
            "symbol_types": {
                "class": sum(1 for s in symbols if s["type"] == "class"),
                "function": sum(1 for s in symbols if s["type"] == "function"),
            },
            "symbols": [s["name"] for s in symbols],
        })

    # --- TEST B: Enrichment prompt overhead ---
    # For each file, build the enrichment prompt and measure its size
    prompt_results = []
    for pr in parse_results:
        size_name = pr["size"]
        filepath = tmpdir / f"sample_{size_name}.py"
        skeleton = squeeze(str(filepath))
        symbols = parse_skeleton_symbols(skeleton)

        if symbols:
            prompt = build_enrichment_prompt(f"sample_{size_name}.py", symbols)
            prompt_tokens = count_tokens_bytes4(prompt)
            skeleton_tokens = count_tokens_bytes4(skeleton)

            prompt_results.append({
                "size": size_name,
                "skeleton_tokens": skeleton_tokens,
                "prompt_tokens": prompt_tokens,
                "prompt_overhead_pct": round(
                    (prompt_tokens - skeleton_tokens) / skeleton_tokens * 100, 1
                ) if skeleton_tokens > 0 else 0,
                "symbols_in_prompt": len(symbols),
            })

    # --- TEST C: Enriched vs plain skeleton size ---
    # Simulate enrichment by adding mock summaries
    enrich_comparison = []
    for pr in parse_results:
        size_name = pr["size"]
        filepath = tmpdir / f"sample_{size_name}.py"
        skeleton = squeeze(str(filepath))
        symbols = parse_skeleton_symbols(skeleton)

        if symbols:
            # Create mock enrichments (realistic 8-12 word summaries)
            mock_enrichments = {}
            for s in symbols:
                mock_enrichments[s["name"]] = f"Processes {s['type']} data through validation and transformation pipeline."

            enriched = merge_enrichments(skeleton, mock_enrichments)
            plain_bytes = len(skeleton.encode("utf-8"))
            enriched_bytes = len(enriched.encode("utf-8"))
            overhead = enriched_bytes - plain_bytes

            enrich_comparison.append({
                "size": size_name,
                "plain_bytes": plain_bytes,
                "enriched_bytes": enriched_bytes,
                "overhead_bytes": overhead,
                "overhead_pct": round(overhead / plain_bytes * 100, 1) if plain_bytes > 0 else 0,
                "symbols_enriched": len(mock_enrichments),
            })

    # --- TEST D: Enrichment cache behavior ---
    cache = EnrichmentCache()
    cache_ops = []

    # Put, get (hit), get with wrong mtime (miss), invalidate
    cache.put("test.py", 1000.0, {"foo": "Does stuff"})
    hit = cache.get("test.py", 1000.0)
    cache_ops.append({"op": "put+get (same mtime)", "result": "HIT" if hit else "MISS"})

    miss = cache.get("test.py", 2000.0)
    cache_ops.append({"op": "get (different mtime)", "result": "HIT" if miss else "MISS"})

    cache.invalidate("test.py")
    after_inv = cache.get("test.py", 1000.0)
    cache_ops.append({"op": "get after invalidate", "result": "HIT" if after_inv else "MISS"})

    # --- TEST E: MCTS session management ---
    mcts_results = {}
    try:
        from mcts import MCTSSession, MCTSSessionManager

        mgr = MCTSSessionManager()
        session_id = mgr.create("How does auth work?", max_depth=5)
        session = mgr.get(session_id)

        # Simulate exploration
        session.visit("src/auth.py::validate_token")
        session.set_score("src/auth.py::validate_token", 0.9)
        session.visit("src/auth.py::check_perms")
        session.set_score("src/auth.py::check_perms", 0.7)
        session.blacklist_node("src/utils.py::format_date")
        session.visit("src/middleware.py::auth_middleware")
        session.set_score("src/middleware.py::auth_middleware", 0.85)
        session.add_context("Found: validate_token checks JWT signature")

        mcts_results = {
            "available": True,
            "depth": session.depth,
            "max_depth": session.max_depth,
            "visited": len(session.visited),
            "blacklisted": len(session.blacklist),
            "context_items": len(session.context_accumulated),
            "top_score": max(session.scores.values()) if session.scores else 0,
        }
    except ImportError:
        mcts_results = {"available": False, "reason": "mcts module not found"}

    # --- TEST F: Agent prompt construction ---
    agent_results = {}
    try:
        from agents.explorer import build_explorer_prompt
        from agents.validator import build_validator_prompt
        from agents.orchestrator import build_orchestrator_prompt

        # Build sample prompts
        session_state = {
            "visited": ["src/auth.py::validate_token"],
            "blacklist": [],
            "depth": 1,
            "max_depth": 5,
            "scores": {"src/auth.py": 0.9},
        }
        explorer_prompt = build_explorer_prompt(
            query="How does auth work?",
            tree_skeleton="src/\n  auth.py\n  middleware.py",
            session_state=session_state,
        )
        validator_prompt = build_validator_prompt(
            query="How does auth work?",
            code_snippet="def validate_token(token): return jwt.decode(token)",
            symbol_path="src/auth.py::validate_token",
        )
        orchestrator_prompt = build_orchestrator_prompt(
            query="How does auth work?",
            session_state=session_state,
            last_result={"is_valid": True, "confidence": 0.9},
        )

        agent_results = {
            "available": True,
            "explorer_prompt_tokens": count_tokens_bytes4(explorer_prompt),
            "validator_prompt_tokens": count_tokens_bytes4(validator_prompt),
            "orchestrator_prompt_tokens": count_tokens_bytes4(orchestrator_prompt),
            "total_agent_overhead": count_tokens_bytes4(
                explorer_prompt + validator_prompt + orchestrator_prompt
            ),
        }
    except (ImportError, Exception) as e:
        agent_results = {"available": False, "reason": str(e)}

    # --- TEST G: Live enrichment ---
    # Strategy: try daemon first (has Claude Code auth token), then direct API
    live_enrichment = None

    # Try via running daemon (uses Claude Code's ANTHROPIC_AUTH_TOKEN)
    daemon_port = _find_daemon_port()

    # If no enrichment-enabled daemon found but we have a token, start one
    started_daemon = False
    if not daemon_port:
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if auth_token:
            print("    Starting daemon with auth token...", end=" ", flush=True)
            daemon_port = _start_daemon_with_token(auth_token)
            if daemon_port:
                print(f"port {daemon_port}")
                started_daemon = True
                # Give it a moment to index files
                time.sleep(0.5)
            else:
                print("failed")

    if daemon_port:
        try:
            # Copy a test file into the daemon's watched root so it can squeeze it
            # We'll use a file from the daemon's own project root instead
            test_file_rel = _find_daemon_test_file(daemon_port)
            if test_file_rel:
                start = time.time()
                result = _query_daemon(daemon_port, {
                    "action": "enrich",
                    "path": test_file_rel,
                })
                elapsed = time.time() - start

                if "error" not in result:
                    skeleton = result.get("skeleton", "")
                    enriched = result.get("enriched_skeleton", "")
                    enrichments = result.get("enrichments", {})

                    live_enrichment = {
                        "via": "daemon (Claude Code auth)",
                        "provider": result.get("provider", "unknown"),
                        "model": result.get("model", "unknown"),
                        "symbols_sent": result.get("symbols_sent", 0),
                        "symbols_enriched": result.get("symbols_enriched", 0),
                        "latency_s": round(elapsed, 2),
                        "plain_skeleton_tokens": count_tokens_bytes4(skeleton),
                        "enriched_skeleton_tokens": count_tokens_bytes4(enriched),
                        "enrichment_overhead_tokens": (
                            count_tokens_bytes4(enriched) -
                            count_tokens_bytes4(skeleton)
                        ),
                        "sample_enrichments": dict(list(enrichments.items())[:3]) if enrichments else {},
                    }
                else:
                    live_enrichment = {"error": f"Daemon: {result['error']}"}
        except Exception as e:
            live_enrichment = {"error": f"Daemon connection: {str(e)}"}

    # Fallback: try direct API call if daemon didn't work
    if live_enrichment is None or "error" in live_enrichment:
        daemon_err = live_enrichment.get("error", "") if live_enrichment else ""
        if config.enrichment_enabled:
            try:
                filepath = tmpdir / "sample_medium.py"
                skeleton = squeeze(str(filepath))
                symbols = parse_skeleton_symbols(skeleton)
                if symbols:
                    prompt = build_enrichment_prompt("sample_medium.py", symbols)
                    start = time.time()
                    raw_response = call_enrichment_api(prompt, config)
                    elapsed = time.time() - start

                    if raw_response:
                        from node_enricher import _parse_enrichment_response
                        enrichments = _parse_enrichment_response(raw_response)
                        enriched_skeleton = merge_enrichments(skeleton, enrichments)

                        live_enrichment = {
                            "via": "direct API",
                            "provider": config.enrichment_provider,
                            "model": config.enrichment_model,
                            "symbols_sent": len(symbols),
                            "symbols_enriched": len(enrichments) if enrichments else 0,
                            "latency_s": round(elapsed, 2),
                            "prompt_tokens": count_tokens_bytes4(prompt),
                            "response_tokens": count_tokens_bytes4(raw_response),
                            "plain_skeleton_tokens": count_tokens_bytes4(skeleton),
                            "enriched_skeleton_tokens": count_tokens_bytes4(enriched_skeleton),
                            "enrichment_overhead_tokens": (
                                count_tokens_bytes4(enriched_skeleton) -
                                count_tokens_bytes4(skeleton)
                            ),
                            "sample_enrichments": dict(list(enrichments.items())[:3]) if enrichments else {},
                        }
            except Exception as e:
                live_enrichment = {
                    "error": f"Direct API: {str(e)}" + (f" (daemon: {daemon_err})" if daemon_err else "")
                }
        elif daemon_err:
            # Keep the daemon error, add note about no direct API fallback
            live_enrichment["note"] = "No ANTHROPIC_API_KEY set for direct fallback"

    return {
        "ai_status": ai_status,
        "parse_results": parse_results,
        "prompt_results": prompt_results,
        "enrich_comparison": enrich_comparison,
        "cache_ops": cache_ops,
        "mcts": mcts_results,
        "agents": agent_results,
        "live_enrichment": live_enrichment,
        "claim": "AI features (enrichment, MCTS, agents) enhance navigation beyond pure AST",
        "verdict": (
            f"LIVE -- enrichment working via {config.enrichment_provider}"
            if live_enrichment and "error" not in (live_enrichment or {})
            else "OFFLINE -- AI features available but no API key configured"
            if mcts_results.get("available")
            else "UNAVAILABLE -- AI modules not importable"
        ),
        "challenger_note": (
            "AI features are OPTIONAL. Core savings come from AST extraction (no AI needed). "
            "Enrichment adds semantic summaries at the cost of API calls + ~20-40% token overhead "
            "on skeletons. The value is faster decision-making, not token savings. "
            "MCTS + agents provide guided exploration but require LLM calls per step."
        ),
    }


def _print_ai_features(test: dict, W: int):
    """Format AI features benchmark results."""
    # AI Status
    status = test["ai_status"]
    print(f"  AI FEATURE STATUS:")
    print(f"    Enrichment enabled:   {'YES' if status['enrichment_enabled'] else 'NO'}")
    if status["enrichment_provider"]:
        print(f"    Provider:             {status['enrichment_provider']}")
        print(f"    Model:                {status['enrichment_model']}")
    print(f"    Anthropic SDK:        {'available' if status['anthropic_available'] else 'not installed'}")
    print(f"    Doc indexing:         {'enabled' if status['doc_indexing_enabled'] else 'disabled'}")
    print()

    # Symbol parsing quality
    print(f"  SKELETON SYMBOL PARSING:")
    headers = ["Size", "File Lines", "Skel Lines", "Symbols", "Classes", "Functions"]
    aligns = ["<", ">", ">", ">", ">", ">"]
    rows = []
    for pr in test["parse_results"]:
        rows.append([
            pr["size"],
            pr["file_lines"],
            pr["skeleton_lines"],
            pr["symbols_found"],
            pr["symbol_types"]["class"],
            pr["symbol_types"]["function"],
        ])
    print(fmt_table(headers, rows, aligns))
    print()

    # Enrichment prompt overhead
    if test["prompt_results"]:
        print(f"  ENRICHMENT PROMPT OVERHEAD:")
        headers = ["Size", "Skeleton (t)", "Prompt (t)", "Overhead", "Symbols"]
        aligns = ["<", ">", ">", ">", ">"]
        rows = []
        for pr in test["prompt_results"]:
            rows.append([
                pr["size"],
                pr["skeleton_tokens"],
                pr["prompt_tokens"],
                f"{pr['prompt_overhead_pct']:+.1f}%",
                pr["symbols_in_prompt"],
            ])
        print(fmt_table(headers, rows, aligns))
        print()

    # Enriched vs plain
    if test["enrich_comparison"]:
        print(f"  ENRICHED vs PLAIN SKELETON (simulated summaries):")
        headers = ["Size", "Plain (B)", "Enriched (B)", "Overhead", "Enriched Syms"]
        aligns = ["<", ">", ">", ">", ">"]
        rows = []
        for ec in test["enrich_comparison"]:
            rows.append([
                ec["size"],
                f"{ec['plain_bytes']:,}",
                f"{ec['enriched_bytes']:,}",
                f"+{ec['overhead_pct']}%",
                ec["symbols_enriched"],
            ])
        print(fmt_table(headers, rows, aligns))
        avg_overhead = sum(ec["overhead_pct"] for ec in test["enrich_comparison"]) / len(test["enrich_comparison"])
        print(f"\n    Avg enrichment overhead: +{avg_overhead:.1f}% more tokens than plain skeleton")
        print(f"    Trade-off: ~{avg_overhead:.0f}% more tokens buys semantic understanding of each symbol")
        print()

    # Cache behavior
    print(f"  ENRICHMENT CACHE:")
    for op in test["cache_ops"]:
        print(f"    {op['op']:<35} {op['result']}")
    print()

    # MCTS
    mcts = test["mcts"]
    print(f"  MCTS SESSION MANAGEMENT:")
    if mcts.get("available"):
        print(f"    Status:           available")
        print(f"    Depth reached:    {mcts['depth']} / {mcts['max_depth']}")
        print(f"    Nodes visited:    {mcts['visited']}")
        print(f"    Nodes blacklisted:{mcts['blacklisted']}")
        print(f"    Context items:    {mcts['context_items']}")
        print(f"    Top relevance:    {mcts['top_score']}")
    else:
        print(f"    Status:           {mcts.get('reason', 'unavailable')}")
    print()

    # Agents
    agents = test["agents"]
    print(f"  TRIAD AGENT PROMPTS:")
    if agents.get("available"):
        print(f"    Explorer prompt:      {agents['explorer_prompt_tokens']} tokens")
        print(f"    Validator prompt:     {agents['validator_prompt_tokens']} tokens")
        print(f"    Orchestrator prompt:  {agents['orchestrator_prompt_tokens']} tokens")
        print(f"    Total agent overhead: {agents['total_agent_overhead']} tokens per MCTS step")
    else:
        print(f"    Status:               {agents.get('reason', 'unavailable')}")
    print()

    # Live enrichment
    live = test.get("live_enrichment")
    if live and "error" not in live:
        via = live.get("via", "unknown")
        print(f"  LIVE ENRICHMENT TEST (via {via}):")
        print(f"    Provider:           {live['provider']} ({live['model']})")
        print(f"    Latency:            {live['latency_s']}s")
        print(f"    Symbols sent:       {live['symbols_sent']}")
        print(f"    Symbols enriched:   {live['symbols_enriched']}")
        if "prompt_tokens" in live:
            print(f"    Prompt tokens:      {live['prompt_tokens']}")
        if "response_tokens" in live:
            print(f"    Response tokens:    {live['response_tokens']}")
        print(f"    Plain skeleton:     {live['plain_skeleton_tokens']} tokens")
        print(f"    Enriched skeleton:  {live['enriched_skeleton_tokens']} tokens")
        print(f"    Enrichment cost:    +{live['enrichment_overhead_tokens']} tokens")
        print()

        if live.get("sample_enrichments"):
            print(f"    SAMPLE ENRICHMENTS:")
            for sym, summary in live["sample_enrichments"].items():
                print(f"      {sym}: {summary}")
    elif live and "error" in live:
        print(f"  LIVE ENRICHMENT: FAILED")
        print(f"    {live['error']}")
        if live.get("note"):
            print(f"    {live['note']}")
    else:
        print(f"  LIVE ENRICHMENT: SKIPPED")
        print(f"    No running daemon found AND no ANTHROPIC_API_KEY set.")
        print(f"    Option 1: Start daemon via Claude Code (uses your subscription)")
        print(f"    Option 2: Set ANTHROPIC_API_KEY env var for direct API calls")


# ---------------------------------------------------------------------------
# Real repo benchmark
# ---------------------------------------------------------------------------

# Well-known large repos with approximate stats
KNOWN_REPOS = {
    "cpython":     ("https://github.com/python/cpython.git",        "~3,800 files, ~750K lines Python/C"),
    "vscode":      ("https://github.com/microsoft/vscode.git",      "~12,000 files, TypeScript-heavy"),
    "kubernetes":  ("https://github.com/kubernetes/kubernetes.git",  "~15,000 files, Go-heavy"),
    "django":      ("https://github.com/django/django.git",         "~2,500 files, Python"),
    "fastapi":     ("https://github.com/fastapi/fastapi.git",       "~200 files, Python"),
    "react":       ("https://github.com/facebook/react.git",        "~2,000 files, JavaScript"),
    "linux":       ("https://github.com/torvalds/linux.git",        "~80,000 files, C (HUGE - slow clone)"),
    "rust":        ("https://github.com/rust-lang/rust.git",        "~30,000 files, Rust"),
    "transformers":("https://github.com/huggingface/transformers.git","~5,000 files, Python ML"),
    "flask":       ("https://github.com/pallets/flask.git",         "~200 files, Python (fast)"),
}

DEFAULT_REPO = "cpython"


def _clone_repo(repo_spec: str, target_dir: Path) -> tuple[Path, str]:
    """Clone or resolve a repo. Returns (path, display_name).

    repo_spec can be:
      - A known short name: "cpython", "vscode", etc.
      - A git URL: "https://github.com/org/repo.git"
      - A local path: "/path/to/existing/repo"
    """
    import subprocess

    # Local path?
    local = Path(repo_spec)
    if local.is_dir() and (local / ".git").exists():
        return local, local.name

    # Known repo name?
    if repo_spec in KNOWN_REPOS:
        url, desc = KNOWN_REPOS[repo_spec]
        name = repo_spec
        print(f"  Repo: {name} ({desc})")
    else:
        url = repo_spec
        name = url.rstrip("/").split("/")[-1].replace(".git", "")
        print(f"  Repo: {url}")

    clone_dir = target_dir / name
    if clone_dir.exists():
        return clone_dir, name

    print(f"  Cloning {name} (shallow, depth=1)...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", url, str(clone_dir)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    print(f"  Cloned to {clone_dir}")
    return clone_dir, name


def _scan_repo_files(repo_path: Path, max_files: int = 5000) -> list[dict]:
    """Walk repo and collect file metadata. Returns sorted by size desc."""
    IGNORED_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", "target", ".tox", ".eggs",
        "vendor", "third_party", "3rdparty", ".mypy_cache",
    }
    SUPPORTED_EXTS = set(
        k for k in (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".cc", ".hpp")
    )

    files = []
    for dirpath, dirnames, filenames in os.walk(str(repo_path)):
        # Prune ignored directories
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                continue
            if size < 50 or size > 500_000:  # Skip tiny/huge files
                continue
            rel = os.path.relpath(fpath, str(repo_path)).replace("\\", "/")
            files.append({
                "path": fpath,
                "rel_path": rel,
                "ext": ext,
                "size": size,
            })
            if len(files) >= max_files:
                break

    files.sort(key=lambda f: f["size"], reverse=True)
    return files


def _categorize_by_size(files: list[dict]) -> dict[str, list[dict]]:
    """Bucket files into size categories."""
    buckets = {
        "small (50-200B)": [],
        "medium (200-2KB)": [],
        "large (2-10KB)": [],
        "xlarge (10-50KB)": [],
        "huge (50KB+)": [],
    }
    for f in files:
        s = f["size"]
        if s < 200:
            buckets["small (50-200B)"].append(f)
        elif s < 2000:
            buckets["medium (200-2KB)"].append(f)
        elif s < 10000:
            buckets["large (2-10KB)"].append(f)
        elif s < 50000:
            buckets["xlarge (10-50KB)"].append(f)
        else:
            buckets["huge (50KB+)"].append(f)
    return buckets


def test_real_repo(repo_spec: str, tmpdir: Path) -> dict:
    """TEST 11: Benchmark against a real, large open-source repository.

    Clones the repo (shallow), scans supported files, samples across size
    buckets, runs squeeze/find_symbol on real code, and reports results.
    """
    import random
    import subprocess

    repo_path, repo_name = _clone_repo(repo_spec, tmpdir)

    # Scan files
    print(f"  Scanning files...", end=" ", flush=True)
    all_files = _scan_repo_files(repo_path)
    print(f"found {len(all_files)} supported source files")

    # Categorize by size
    buckets = _categorize_by_size(all_files)

    # Count by language
    lang_counts: dict[str, int] = {}
    for f in all_files:
        ext = f["ext"]
        lang_counts[ext] = lang_counts.get(ext, 0) + 1

    # Sample files from each bucket (up to 15 per bucket)
    SAMPLE_PER_BUCKET = 15
    sampled: list[dict] = []
    for bucket_name, bucket_files in buckets.items():
        if not bucket_files:
            continue
        sample = random.sample(bucket_files, min(SAMPLE_PER_BUCKET, len(bucket_files)))
        for f in sample:
            f["bucket"] = bucket_name
        sampled.extend(sample)

    # --- TEST A: Skeleton extraction on real files ---
    print(f"  Running skeleton extraction on {len(sampled)} sampled files...", end=" ", flush=True)
    skeleton_results = []
    errors = 0
    for f in sampled:
        try:
            content = open(f["path"], encoding="utf-8", errors="replace").read()
            skeleton = squeeze(f["path"])
            full_bytes = len(content.encode("utf-8"))
            skel_bytes = len(skeleton.encode("utf-8"))

            is_error = skeleton.startswith("Error") or skeleton.startswith("unsupported")
            if is_error:
                errors += 1
                continue

            savings_pct = ((full_bytes - skel_bytes) / full_bytes * 100) if full_bytes > 0 else 0

            skeleton_results.append({
                "rel_path": f["rel_path"],
                "ext": f["ext"],
                "bucket": f["bucket"],
                "full_bytes": full_bytes,
                "skeleton_bytes": skel_bytes,
                "lines": content.count("\n") + 1,
                "savings_pct": round(savings_pct, 1),
                "ratio": round(full_bytes / skel_bytes, 1) if skel_bytes > 0 else 0,
            })
        except Exception:
            errors += 1
    print(f"done ({len(skeleton_results)} ok, {errors} errors)")

    # --- TEST B: Drill savings on real symbols ---
    print(f"  Running drill tests...", end=" ", flush=True)
    drill_results = []
    # Pick files with good skeletons and try to find symbols
    drill_candidates = [r for r in skeleton_results if r["savings_pct"] > 30 and r["lines"] > 50]
    random.shuffle(drill_candidates)

    for r in drill_candidates[:20]:  # Test up to 20 files
        fpath = repo_path / r["rel_path"].replace("/", os.sep)
        content = open(str(fpath), encoding="utf-8", errors="replace").read()
        skeleton = squeeze(str(fpath))

        # Extract symbol names from skeleton (look for def/class/func patterns)
        import re as _re
        sym_matches = _re.findall(r'(?:def|class|func|function|fn)\s+(\w+)', skeleton)
        if not sym_matches:
            continue

        # Try the first symbol
        sym = sym_matches[0]
        result = find_symbol(str(fpath), sym)
        if result:
            start, end = result
            lines = content.splitlines()
            drilled = "\n".join(lines[start - 1:end])
            drilled_bytes = len(drilled.encode("utf-8"))
            full_bytes = len(content.encode("utf-8"))

            savings_pct = ((full_bytes - drilled_bytes) / full_bytes * 100) if full_bytes > 0 else 0

            drill_results.append({
                "rel_path": r["rel_path"],
                "symbol": sym,
                "file_lines": r["lines"],
                "drilled_lines": end - start + 1,
                "full_bytes": full_bytes,
                "drilled_bytes": drilled_bytes,
                "savings_pct": round(savings_pct, 1),
            })
    print(f"done ({len(drill_results)} symbols drilled)")

    # --- TEST C: End-to-end workflow simulation on real files ---
    print(f"  Running e2e workflow simulation...", end=" ", flush=True)

    # Pick a common identifier from the codebase
    # Sample a few skeleton outputs and find a recurring name
    common_syms: dict[str, int] = {}
    for r in skeleton_results[:50]:
        fpath = repo_path / r["rel_path"].replace("/", os.sep)
        skeleton = squeeze(str(fpath))
        import re as _re
        for sym in _re.findall(r'(?:def|class|func|function|fn)\s+(\w+)', skeleton):
            if len(sym) > 3 and sym not in ("self", "init", "main", "test", "setup", "None"):
                common_syms[sym] = common_syms.get(sym, 0) + 1

    # Pick a symbol that appears in 3-10 files (interesting but not ubiquitous)
    query_candidates = [(sym, count) for sym, count in common_syms.items() if 3 <= count <= 10]
    if not query_candidates:
        query_candidates = [(sym, count) for sym, count in common_syms.items() if count >= 2]

    e2e_result = None
    if query_candidates:
        query_candidates.sort(key=lambda x: -x[1])
        chosen_query = query_candidates[0][0]

        # Traditional: grep + read full files
        trad_tokens = 0
        matching = []
        for r in skeleton_results:
            fpath = repo_path / r["rel_path"].replace("/", os.sep)
            try:
                content = open(str(fpath), encoding="utf-8", errors="replace").read()
                if chosen_query in content:
                    matching.append((r["rel_path"], content))
            except Exception:
                pass

        # grep cost (matching lines only)
        grep_lines = []
        for rel_path, content in matching:
            for i, line in enumerate(content.splitlines(), 1):
                if chosen_query in line:
                    grep_lines.append(f"{rel_path}:{i}:{line.strip()}")
        grep_output = "\n".join(grep_lines)
        trad_tokens += count_tokens_bytes4(grep_output)

        # read full files
        for rel_path, content in matching:
            trad_tokens += count_tokens_bytes4(content)

        # RLM: search + map + drill
        rlm_tokens = 0

        # search result
        search_lines_out = []
        matched_for_map = []
        for r in skeleton_results:
            fpath = repo_path / r["rel_path"].replace("/", os.sep)
            skeleton = squeeze(str(fpath))
            if chosen_query in skeleton:
                matched_for_map.append((r["rel_path"], str(fpath)))
                for line in skeleton.splitlines():
                    if chosen_query in line:
                        search_lines_out.append(f"{r['rel_path']}: {line.strip()}")
        search_out = "\n".join(search_lines_out)
        rlm_tokens += count_tokens_bytes4(search_out)

        # map each
        for rel_path, fpath in matched_for_map:
            skeleton = squeeze(fpath)
            rlm_tokens += count_tokens_bytes4(skeleton)

        # drill first 5
        for rel_path, fpath in matched_for_map[:5]:
            result = find_symbol(fpath, chosen_query)
            if result:
                start, end = result
                content = open(fpath, encoding="utf-8", errors="replace").read()
                lines = content.splitlines()
                drilled = "\n".join(lines[start - 1:end])
                rlm_tokens += count_tokens_bytes4(drilled)

        saved = trad_tokens - rlm_tokens
        pct = (saved / trad_tokens * 100) if trad_tokens > 0 else 0

        e2e_result = {
            "query": chosen_query,
            "matching_files": len(matching),
            "trad_tokens": trad_tokens,
            "rlm_tokens": rlm_tokens,
            "saved_tokens": saved,
            "savings_pct": round(pct, 1),
            "ratio": round(trad_tokens / rlm_tokens, 1) if rlm_tokens > 0 else 0,
        }
    print("done")

    # --- Aggregate statistics ---
    if skeleton_results:
        avg_savings = sum(r["savings_pct"] for r in skeleton_results) / len(skeleton_results)
        median_idx = len(skeleton_results) // 2
        sorted_by_savings = sorted(skeleton_results, key=lambda r: r["savings_pct"])
        median_savings = sorted_by_savings[median_idx]["savings_pct"]
        min_savings = sorted_by_savings[0]["savings_pct"]
        max_savings = sorted_by_savings[-1]["savings_pct"]
        p10 = sorted_by_savings[len(sorted_by_savings) // 10]["savings_pct"]
        p90 = sorted_by_savings[9 * len(sorted_by_savings) // 10]["savings_pct"]
    else:
        avg_savings = median_savings = min_savings = max_savings = p10 = p90 = 0

    # Per-bucket stats
    bucket_stats = {}
    for bucket_name in buckets:
        bucket_results = [r for r in skeleton_results if r["bucket"] == bucket_name]
        if bucket_results:
            bucket_avg = sum(r["savings_pct"] for r in bucket_results) / len(bucket_results)
            bucket_stats[bucket_name] = {
                "count": len(bucket_results),
                "avg_savings": round(bucket_avg, 1),
                "min_savings": min(r["savings_pct"] for r in bucket_results),
                "max_savings": max(r["savings_pct"] for r in bucket_results),
            }

    # Per-language stats
    lang_stats = {}
    for r in skeleton_results:
        ext = r["ext"]
        if ext not in lang_stats:
            lang_stats[ext] = {"count": 0, "total_savings": 0, "total_full": 0, "total_skel": 0}
        lang_stats[ext]["count"] += 1
        lang_stats[ext]["total_savings"] += r["savings_pct"]
        lang_stats[ext]["total_full"] += r["full_bytes"]
        lang_stats[ext]["total_skel"] += r["skeleton_bytes"]

    for ext, stats in lang_stats.items():
        stats["avg_savings"] = round(stats["total_savings"] / stats["count"], 1)
        byte_savings = ((stats["total_full"] - stats["total_skel"]) / stats["total_full"] * 100) if stats["total_full"] > 0 else 0
        stats["weighted_savings"] = round(byte_savings, 1)

    # Top 10 biggest files with their savings
    top_files = sorted(skeleton_results, key=lambda r: r["full_bytes"], reverse=True)[:10]

    return {
        "repo_name": repo_name,
        "repo_path": str(repo_path),
        "total_source_files": len(all_files),
        "sampled_files": len(sampled),
        "successful_squeezes": len(skeleton_results),
        "squeeze_errors": errors,
        "lang_distribution": dict(sorted(lang_counts.items(), key=lambda x: -x[1])),

        # Skeleton stats
        "avg_savings": round(avg_savings, 1),
        "median_savings": median_savings,
        "min_savings": min_savings,
        "max_savings": max_savings,
        "p10_savings": p10,
        "p90_savings": p90,
        "bucket_stats": bucket_stats,
        "lang_stats": {k: v for k, v in sorted(lang_stats.items(), key=lambda x: -x[1]["count"])},
        "top_files": top_files,

        # Drill stats
        "drill_results": drill_results,
        "avg_drill_savings": round(sum(r["savings_pct"] for r in drill_results) / len(drill_results), 1) if drill_results else 0,

        # E2E workflow
        "e2e": e2e_result,

        "claim": f"RLM savings hold on real-world code ({repo_name})",
        "verdict": (
            f"CONFIRMED -- {round(avg_savings, 0)}% avg skeleton reduction on {len(skeleton_results)} real files"
            if avg_savings > 50 else
            f"PARTIAL -- {round(avg_savings, 0)}% avg skeleton reduction"
        ),
        "challenger_note": (
            f"Tested against {repo_name} ({len(all_files)} source files). "
            f"Sampled {len(sampled)} files across all size buckets. "
            f"Skeleton extraction averaged {avg_savings:.1f}% savings "
            f"(median {median_savings}%, range {min_savings}-{max_savings}%). "
            f"These are REAL files with real-world complexity, not synthetic benchmarks."
        ),
    }


def _print_real_repo(test: dict, W: int):
    """Format real repo benchmark results."""
    print(f"  Repository: {test['repo_name']}")
    print(f"  Source files found: {test['total_source_files']:,}")
    print(f"  Files sampled: {test['sampled_files']}")
    print(f"  Successfully squeezed: {test['successful_squeezes']} (errors: {test['squeeze_errors']})")
    print()

    # Language distribution
    print(f"  LANGUAGE DISTRIBUTION:")
    lang_dist = test["lang_distribution"]
    max_lang_count = max(lang_dist.values()) if lang_dist else 1
    for ext, count in list(lang_dist.items())[:8]:
        bar = fmt_bar(count, max_lang_count, 25)
        print(f"    {ext:>5}  [{bar}] {count:>5} files")
    if len(lang_dist) > 8:
        print(f"    ... and {len(lang_dist) - 8} more extensions")
    print()

    # Overall skeleton stats
    print(f"  SKELETON EXTRACTION STATISTICS:")
    print(f"    Average savings:  {test['avg_savings']}%")
    print(f"    Median savings:   {test['median_savings']}%")
    print(f"    Range:            {test['min_savings']}% - {test['max_savings']}%")
    print(f"    P10/P90:          {test['p10_savings']}% / {test['p90_savings']}%")
    print()

    # Per-bucket stats
    print(f"  SAVINGS BY FILE SIZE:")
    headers = ["Size Bucket", "Files", "Avg Savings", "Min", "Max"]
    aligns = ["<", ">", ">", ">", ">"]
    rows = []
    for bucket_name, stats in test["bucket_stats"].items():
        rows.append([
            bucket_name,
            stats["count"],
            f"{stats['avg_savings']}%",
            f"{stats['min_savings']}%",
            f"{stats['max_savings']}%",
        ])
    print(fmt_table(headers, rows, aligns))
    print()

    # Per-language stats
    print(f"  SAVINGS BY LANGUAGE:")
    headers = ["Ext", "Files", "Avg Savings", "Weighted*", "Total Full", "Total Skel"]
    aligns = ["<", ">", ">", ">", ">", ">"]
    rows = []
    for ext, stats in test["lang_stats"].items():
        rows.append([
            ext,
            stats["count"],
            f"{stats['avg_savings']}%",
            f"{stats['weighted_savings']}%",
            f"{stats['total_full']:,}B",
            f"{stats['total_skel']:,}B",
        ])
    print(fmt_table(headers, rows, aligns))
    print(f"    * Weighted = total bytes saved / total bytes (accounts for file size)")
    print()

    # Top 10 biggest files
    print(f"  TOP 10 LARGEST FILES:")
    headers = ["File", "Lines", "Full", "Skeleton", "Savings"]
    aligns = ["<", ">", ">", ">", ">"]
    rows = []
    for f in test["top_files"]:
        name = f["rel_path"]
        if len(name) > 45:
            name = "..." + name[-42:]
        rows.append([
            name,
            f["lines"],
            f"{f['full_bytes']:,}B",
            f"{f['skeleton_bytes']:,}B",
            f"{f['savings_pct']}%",
        ])
    print(fmt_table(headers, rows, aligns))
    print()

    # Bar chart: largest files
    if test["top_files"]:
        max_bytes = test["top_files"][0]["full_bytes"]
        for f in test["top_files"][:5]:
            name = f["rel_path"].split("/")[-1]
            if len(name) > 20:
                name = name[:17] + "..."
            full_bar = fmt_bar(f["full_bytes"], max_bytes, 30)
            skel_bar = fmt_bar(f["skeleton_bytes"], max_bytes, 30)
            print(f"    {name:<20} Full: [{full_bar}] {f['full_bytes']:>8,}B")
            print(f"    {'':20} Skel: [{skel_bar}] {f['skeleton_bytes']:>8,}B  ({f['savings_pct']}%)")
        print()

    # Drill results
    if test["drill_results"]:
        print(f"  SURGICAL DRILL (real symbols):")
        print(f"    Average drill savings: {test['avg_drill_savings']}%")
        headers = ["File", "Symbol", "File Lines", "Drilled", "Savings"]
        aligns = ["<", "<", ">", ">", ">"]
        rows = []
        for r in test["drill_results"][:10]:
            name = r["rel_path"].split("/")[-1]
            if len(name) > 25:
                name = name[:22] + "..."
            rows.append([
                name,
                r["symbol"][:20],
                r["file_lines"],
                r["drilled_lines"],
                f"{r['savings_pct']}%",
            ])
        print(fmt_table(headers, rows, aligns))
        print()

    # E2E workflow
    e2e = test.get("e2e")
    if e2e:
        print(f"  END-TO-END WORKFLOW (real query):")
        print(f"    Query: \"{e2e['query']}\" ({e2e['matching_files']} matching files)")
        print()

        max_val = max(e2e["trad_tokens"], e2e["rlm_tokens"])
        print(f"    Traditional: [{fmt_bar(e2e['trad_tokens'], max_val, 35)}] {e2e['trad_tokens']:,} tokens")
        print(f"    RLM:         [{fmt_bar(e2e['rlm_tokens'], max_val, 35)}] {e2e['rlm_tokens']:,} tokens")
        print()
        print(f"    Tokens saved: {e2e['saved_tokens']:,} ({e2e['savings_pct']}% reduction)")
        print(f"    Efficiency:   {e2e['ratio']}x less context consumed")


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def fmt_bar(value: int, max_value: int, width: int = 40, char: str = "#") -> str:
    """Create a text bar chart."""
    if max_value <= 0:
        return " " * width
    filled = max(1, int(width * value / max_value))
    return char * filled + " " * (width - filled)


def fmt_table(headers: list[str], rows: list[list], alignments: Optional[list[str]] = None) -> str:
    """Format a simple text table."""
    if not rows:
        return ""

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    if alignments is None:
        alignments = ["<"] * len(headers)

    # Header
    header = " | ".join(f"{h:{a}{w}}" for h, w, a in zip(headers, widths, alignments))
    separator = "-+-".join("-" * w for w in widths)

    # Rows
    lines = [f" {header}", f" {separator}"]
    for row in rows:
        cells = []
        for i, (cell, w, a) in enumerate(zip(row, widths, alignments)):
            cells.append(f"{str(cell):{a}{w}}")
        lines.append(f" {' | '.join(cells)}")

    return "\n".join(lines)


def print_report(results: BenchmarkResults):
    """Print the full benchmark report."""
    W = 78
    THICK = "=" * W
    THIN = "-" * W

    print()
    print(THICK)
    print(f"{'RLM Navigator -- Token Savings Benchmark Report':^{W}}")
    print(THICK)
    print(f"  Date: {time.strftime('%Y-%m-%d %H:%M')}")
    langs = supported_languages()
    print(f"  Supported languages: {', '.join(sorted(langs)) if langs else 'none detected'}")
    print(f"  Tests run: {len(results.tests)}")
    print(THICK)
    print()

    for test in results.tests:
        name = test["name"]
        print(f"  {'-' * (W - 4)}")
        print(f"  TEST: {name}")
        print(f"  {'-' * (W - 4)}")
        print(f"  Claim: {test.get('claim', 'N/A')}")
        print(f"  Verdict: {test.get('verdict', 'N/A')}")
        print()

        # Skip detailed formatting for errored tests
        if "error" in test:
            print(f"  Error: {test['error']}")
            print()
            continue

        # Dispatch to per-test formatters
        if name == "skeleton_by_size":
            _print_skeleton_size(test, W)
        elif name == "skeleton_by_language":
            _print_skeleton_lang(test, W)
        elif name == "drill_savings":
            _print_drill(test, W)
        elif name == "search_fair_comparison":
            _print_search(test, W)
        elif name == "tree_comparison":
            _print_tree(test, W)
        elif name == "doc_navigation":
            _print_doc(test, W)
        elif name == "chunk_savings":
            _print_chunks(test, W)
        elif name == "scaling_analysis":
            _print_scaling(test, W)
        elif name == "token_estimation":
            _print_tokens(test, W)
        elif name == "e2e_workflow":
            _print_e2e(test, W)
        elif name == "real_repo":
            _print_real_repo(test, W)
        elif name == "ai_features":
            _print_ai_features(test, W)

        if "challenger_note" in test:
            print()
            print(f"  !! CHALLENGER NOTE:")
            for line in textwrap.wrap(test["challenger_note"], W - 6):
                print(f"    {line}")
        print()

    # Summary
    print(THICK)
    print(f"{'SUMMARY':^{W}}")
    print(THICK)
    print()

    confirmed = sum(1 for t in results.tests if "CONFIRMED" in t.get("verdict", ""))
    partial = sum(1 for t in results.tests if "PARTIAL" in t.get("verdict", "") or "COMPARABLE" in t.get("verdict", ""))
    overstated = sum(1 for t in results.tests if "OVERSTATED" in t.get("verdict", ""))
    acknowledged = sum(1 for t in results.tests if "ACKNOWLEDGED" in t.get("verdict", ""))

    print(f"  CONFIRMED:    {confirmed} tests -savings claim validated")
    print(f"  PARTIAL:      {partial} tests -savings exist but with caveats")
    print(f"  OVERSTATED:   {overstated} tests -savings methodology unfair")
    print(f"  ACKNOWLEDGED: {acknowledged} tests -limitation documented")
    print()

    # Key findings
    print(f"  KEY FINDINGS:")
    print(f"  {THIN[4:]}")

    # Extract skeleton savings from scaling test
    scaling = next((t for t in results.tests if t["name"] == "scaling_analysis"), None)
    if scaling:
        bp = scaling.get("breakeven_lines", "?")
        wp = scaling.get("worthwhile_lines", "?")
        print(f"  1. Skeleton extraction breakeven: ~{bp} lines; >50% savings at ~{wp} lines")

    # Extract e2e results
    e2e = next((t for t in results.tests if t["name"] == "e2e_workflow"), None)
    if e2e:
        pct = e2e.get("savings_pct", 0)
        ratio = e2e.get("ratio", 0)
        print(f"  2. End-to-end workflow: {pct}% fewer tokens ({ratio}x efficiency)")

    # Note search fairness
    search = next((t for t in results.tests if t["name"] == "search_fair_comparison"), None)
    if search:
        fc = search.get("fair_comparison", {})
        print(f"  3. Search: rlm_search ({fc.get('rlm_search_bytes', 0)}B) ~= grep ({fc.get('grep_output_bytes', 0)}B)")
        print(f"     Daemon's claimed savings are vs full file reads (overstated)")

    # Note token estimation
    print(f"  4. Token estimation (bytes/4): percentages valid, absolute counts +/-30-50%")
    print(f"  5. Small files (<50 lines): skeleton overhead exceeds savings; read directly")
    print()

    print(f"  METHODOLOGY NOTES:")
    print(f"  {THIN[4:]}")
    print(f"  - All tests use synthetic but realistic code (not toy examples)")
    print(f"  - Token estimates: bytes/4 (same as daemon, for consistency)")
    print(f"  - 'Traditional' baseline: grep + full file read (worst case for traditional)")
    print(f"  - Search comparison: fair (vs grep output, not vs full file reads)")
    print(f"  - Daemon startup overhead (~500-1000 tokens) not included in per-test results")
    print(f"  - No network or caching effects measured (local function calls only)")
    print()
    print(THICK)


def _print_skeleton_size(test: dict, W: int):
    headers = ["Size", "Lines", "Full (B)", "Skeleton (B)", "Savings", "Ratio"]
    aligns = ["<", ">", ">", ">", ">", ">"]
    rows = []
    for r in test["rows"]:
        rows.append([
            r["size"],
            r["lines"],
            f"{r['full_bytes']:,}",
            f"{r['skeleton_bytes']:,}",
            f"{r['savings_pct']}%",
            f"{r['ratio']}x",
        ])
    print(fmt_table(headers, rows, aligns))
    print()

    # Visual bar chart
    max_bytes = max(r["full_bytes"] for r in test["rows"])
    for r in test["rows"]:
        full_bar = fmt_bar(r["full_bytes"], max_bytes, 35)
        skel_bar = fmt_bar(r["skeleton_bytes"], max_bytes, 35)
        print(f"  {r['size']:<8} Full: [{full_bar}] {r['full_bytes']:>6,}B")
        print(f"  {'':8} Skel: [{skel_bar}] {r['skeleton_bytes']:>6,}B")


def _print_skeleton_lang(test: dict, W: int):
    headers = ["Language", "Ext", "Lines", "Full (B)", "Skeleton (B)", "Savings", "OK"]
    aligns = ["<", "<", ">", ">", ">", ">", "^"]
    rows = []
    for r in test["rows"]:
        rows.append([
            r["language"],
            r["extension"],
            r["lines"],
            f"{r['full_bytes']:,}",
            f"{r['skeleton_bytes']:,}",
            f"{r['savings_pct']}%" if r["supported"] else "N/A",
            "Y" if r["supported"] else "N",
        ])
    print(fmt_table(headers, rows, aligns))
    print(f"\n  Average savings (supported languages): {test['avg_savings']}%")


def _print_drill(test: dict, W: int):
    headers = ["File Size", "File Lines", "Symbol", "Drilled Lines", "Savings", "Ratio"]
    aligns = ["<", ">", "<", ">", ">", ">"]
    rows = []
    for r in test["rows"]:
        rows.append([
            r["file_size"],
            r["file_lines"],
            r["symbol"],
            r["drilled_lines"],
            f"{r['savings_pct']}%",
            f"{r['ratio']}x",
        ])
    print(fmt_table(headers, rows, aligns))
    print(f"\n  Average drill savings: {test['avg_savings']}%")


def _print_search(test: dict, W: int):
    dc = test["daemon_claim"]
    fc = test["fair_comparison"]

    print(f"  Query: \"{test['query']}\"")
    print(f"  Matching files: {test['matching_files']} / {test['total_files']}")
    print()

    print(f"  DAEMON'S CLAIMED COMPARISON (vs full file reads):")
    print(f"    rlm_search response:  {dc['search_response_bytes']:>6,}B")
    print(f"    Full file reads:      {dc['full_files_bytes']:>6,}B")
    print(f"    Claimed avoided:      {dc['claimed_avoided']:>6,}B ({dc['claimed_savings_pct']}%)")
    print()

    print(f"  FAIR COMPARISON (vs grep output):")
    print(f"    rlm_search response:  {fc['rlm_search_bytes']:>6,}B  ({fc['rlm_search_tokens']} tokens)")
    print(f"    grep matching lines:  {fc['grep_output_bytes']:>6,}B  ({fc['grep_tokens']} tokens)")
    print(f"    Difference:           {fc['difference_bytes']:>6,}B")

    max_val = max(dc["full_files_bytes"], dc["search_response_bytes"], fc["grep_output_bytes"])
    print()
    print(f"    Full reads: [{fmt_bar(dc['full_files_bytes'], max_val, 40)}] {dc['full_files_bytes']:,}B")
    print(f"    grep:       [{fmt_bar(fc['grep_output_bytes'], max_val, 40)}] {fc['grep_output_bytes']:,}B")
    print(f"    rlm_search: [{fmt_bar(fc['rlm_search_bytes'], max_val, 40)}] {fc['rlm_search_bytes']:,}B")


def _print_tree(test: dict, W: int):
    print(f"  Project: {test['files']} files in {test['directories']} directories")
    print()

    max_val = max(test["ls_bytes"], test["tree_bytes"], test["glob_bytes"])
    print(f"  ls -R output:   [{fmt_bar(test['ls_bytes'], max_val, 35)}] {test['ls_bytes']:>5,}B ({test['ls_tokens']} tokens)")
    print(f"  rlm_tree:       [{fmt_bar(test['tree_bytes'], max_val, 35)}] {test['tree_bytes']:>5,}B ({test['tree_tokens']} tokens)")
    print(f"  Glob (paths):   [{fmt_bar(test['glob_bytes'], max_val, 35)}] {test['glob_bytes']:>5,}B ({test['glob_tokens']} tokens)")


def _print_doc(test: dict, W: int):
    print(f"  Document: {test['full_lines']} lines, {test['full_bytes']:,} bytes")
    print(f"  doc_map: {test['map_headers']} headers extracted ({test['map_bytes']:,}B)")
    print(f"  doc_drill (1 section): {test['drill_lines']} lines ({test['drill_bytes']:,}B)")
    print(f"  Combined (map + drill): {test['combined_bytes']:,}B")
    print(f"  Savings: {test['savings_pct']}% ({test['ratio']}x)")
    print()

    max_val = test["full_bytes"]
    print(f"  Full read: [{fmt_bar(test['full_bytes'], max_val, 40)}] {test['full_bytes']:,}B")
    print(f"  Map+Drill: [{fmt_bar(test['combined_bytes'], max_val, 40)}] {test['combined_bytes']:,}B")


def _print_chunks(test: dict, W: int):
    print(f"  File: {test['full_lines']} lines, {test['full_bytes']:,}B")
    print(f"  Chunks: {test['num_chunks']} (size={test['chunk_size']}, overlap={test['overlap']})")
    print()

    max_val = max(test["full_bytes"], test["all_chunks_bytes"])
    print(f"  Full file:    [{fmt_bar(test['full_bytes'], max_val, 40)}] {test['full_bytes']:>7,}B")
    print(f"  All chunks:   [{fmt_bar(test['all_chunks_bytes'], max_val, 40)}] {test['all_chunks_bytes']:>7,}B")
    print(f"  Single chunk: [{fmt_bar(test['single_chunk_bytes'], max_val, 40)}] {test['single_chunk_bytes']:>7,}B")
    print()
    print(f"  Single chunk savings: {test['single_chunk_savings_pct']}%")
    print(f"  All chunks overhead: {test['overhead_ratio']}x full file")


def _print_scaling(test: dict, W: int):
    print(f"  Breakeven (savings > 0%): ~{test.get('breakeven_lines', '?')} lines")
    print(f"  Worthwhile (savings > 50%): ~{test.get('worthwhile_lines', '?')} lines")
    print()

    headers = ["Lines", "Full (B)", "Skeleton (B)", "Raw Savings", "Effective*"]
    aligns = [">", ">", ">", ">", ">"]
    rows = []
    for dp in test["data_points"]:
        rows.append([
            dp["lines"],
            f"{dp['full_bytes']:,}",
            f"{dp['skeleton_bytes']:,}",
            f"{dp['raw_savings_pct']}%",
            f"{dp['effective_savings_pct']}%",
        ])
    print(fmt_table(headers, rows, aligns))
    print()
    print(f"  * Effective savings includes ~80B JSON response overhead")
    print()

    # ASCII art savings curve
    print(f"  SAVINGS CURVE:")
    print(f"  100% |")
    max_lines = max(dp["lines"] for dp in test["data_points"])
    for pct_level in range(100, -1, -10):
        line = f"  {pct_level:3d}% |"
        for dp in test["data_points"]:
            if dp["effective_savings_pct"] >= pct_level:
                line += " ##"
            else:
                line += "   "
        print(line)
    label_line = "       +"
    for dp in test["data_points"]:
        label_line += "---"
    print(label_line)
    label_line2 = "        "
    for dp in test["data_points"]:
        label_line2 += f"{dp['lines']:>3}"[-3:]
    print(label_line2)
    print(f"        {'Lines in file ->':^{len(test['data_points'])*3}}")


def _print_tokens(test: dict, W: int):
    headers = ["Content Type", "Bytes", "Chars", "B/4", "C/4", "Wordsx1.3", "Sym%"]
    aligns = ["<", ">", ">", ">", ">", ">", ">"]
    rows = []
    for r in test["results"]:
        rows.append([
            r["content_type"],
            f"{r['bytes']:,}",
            f"{r['chars']:,}",
            f"{r['est_bytes4']:,}",
            f"{r['est_chars4']:,}",
            f"{r['est_words13']:,}",
            f"{r['symbol_density']}%",
        ])
    print(fmt_table(headers, rows, aligns))
    print()
    print(f"  B/4 = daemon's estimate | C/4 = char-based | Wordsx1.3 = word-based")
    print(f"  Sym% = symbol density (higher = more tokens per char in practice)")


def _print_e2e(test: dict, W: int):
    print(f"  Query: \"{test['query']}\" across {test['total_files']} files ({test['matching_files']} matches)")
    print()

    trad = test["traditional"]
    rlm = test["rlm"]

    print(f"  TRADITIONAL (grep + read full files):")
    for step in trad["steps"]:
        lines = f" ({step['lines']} lines)" if "lines" in step else ""
        print(f"    {step['action']:<55} {step['tokens']:>5}t{lines}")
    print(f"    {'-' * 60}")
    print(f"    {'TOTAL':<55} {trad['total_tokens']:>5}t")
    print()

    print(f"  RLM NAVIGATOR (tree -> search -> map -> drill):")
    for step in rlm["steps"]:
        lines = f" ({step['lines']} lines)" if "lines" in step else ""
        print(f"    {step['action']:<55} {step['tokens']:>5}t{lines}")
    print(f"    {'-' * 60}")
    print(f"    {'TOTAL':<55} {rlm['total_tokens']:>5}t")
    print()

    max_val = max(trad["total_tokens"], rlm["total_tokens"])
    print(f"  COMPARISON:")
    print(f"    Traditional: [{fmt_bar(trad['total_tokens'], max_val, 40)}] {trad['total_tokens']:,}t")
    print(f"    RLM:         [{fmt_bar(rlm['total_tokens'], max_val, 40)}] {rlm['total_tokens']:,}t")
    print()
    print(f"    Tokens saved: {test['savings_tokens']:,} ({test['savings_pct']}% reduction)")
    print(f"    Efficiency:   {test['ratio']}x less context consumed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all_tests(test_filter: str = "all", repo: Optional[str] = None) -> BenchmarkResults:
    """Run all benchmark tests and collect results."""
    results = BenchmarkResults()

    with tempfile.TemporaryDirectory(prefix="rlm_bench_") as tmpdir:
        tmpdir = Path(tmpdir)

        tests = {
            "skeleton": [
                ("skeleton_by_size", test_skeleton_by_size),
                ("skeleton_by_language", test_skeleton_by_language),
            ],
            "drill": [("drill_savings", test_drill_savings)],
            "search": [("search_fair_comparison", test_search_fair_comparison)],
            "tree": [("tree_comparison", test_tree_comparison)],
            "doc": [("doc_navigation", test_doc_navigation)],
            "chunks": [("chunk_savings", test_chunk_savings)],
            "scaling": [("scaling_analysis", test_scaling_analysis)],
            "tokens": [("token_estimation", test_token_estimation)],
            "e2e": [("e2e_workflow", test_end_to_end_workflow)],
            "ai": [("ai_features", test_ai_features)],
        }

        # Add real repo test if requested
        if repo:
            tests["repo"] = [("real_repo", lambda td: test_real_repo(repo, td))]

        if test_filter == "all":
            run_tests = []
            for group in tests.values():
                run_tests.extend(group)
        elif test_filter in tests:
            run_tests = tests[test_filter]
        else:
            print(f"Unknown test: {test_filter}")
            print(f"Available: {', '.join(tests.keys())}, all")
            sys.exit(1)

        for name, func in run_tests:
            try:
                print(f"  Running: {name}...", end=" ", flush=True)
                start = time.time()
                result = func(tmpdir)
                elapsed = time.time() - start
                result["name"] = name
                result["elapsed_s"] = round(elapsed, 2)
                results.add_test(name, result)
                print(f"done ({elapsed:.1f}s)")
            except Exception as e:
                print(f"FAILED: {e}")
                import traceback
                traceback.print_exc()
                results.add_test(name, {
                    "name": name,
                    "error": str(e),
                    "claim": "N/A",
                    "verdict": "ERROR",
                })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="RLM Navigator --Comprehensive Token Savings Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Available tests:
              skeleton  Skeleton extraction by size and language
              drill     Surgical symbol extraction
              search    Fair comparison: rlm_search vs grep
              tree      Directory exploration comparison
              doc       Document navigation savings
              chunks    Chunk read vs full file
              scaling   Savings curve with breakeven analysis
              tokens    Token estimation accuracy
              e2e       End-to-end workflow simulation
              ai        AI features (enrichment, MCTS, agents)
              repo      Real repo benchmark (requires --repo)
              all       Run all tests (default)

            Known repos for --repo:
              cpython, vscode, kubernetes, django, fastapi,
              react, linux, rust, transformers, flask
        """)
    )
    parser.add_argument("--test", default="all",
                       help="Which test to run (default: all)")
    parser.add_argument("--repo", default=None,
                       help="Benchmark against a real repo. Use a known name "
                            "(cpython, vscode, kubernetes, django, fastapi, react, "
                            "linux, rust, transformers, flask), a git URL, or a local path.")
    parser.add_argument("--json", action="store_true",
                       help="Also write results to benchmark_results.json")
    parser.add_argument("--token", default=None,
                       help="Anthropic auth token for live enrichment test. "
                            "If not provided, tries to find a running daemon with "
                            "enrichment enabled (e.g., one started by Claude Code's MCP server).")
    args = parser.parse_args()

    # If token provided, inject it into environment for the daemon/config to find
    if args.token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = args.token

    print()
    print("  RLM Navigator --Token Savings Benchmark")
    print("  " + "=" * 42)
    if args.repo:
        print(f"  Real repo: {args.repo}")
    if args.token:
        print(f"  Auth token: provided ({len(args.token)} chars)")
    print()

    results = run_all_tests(args.test, repo=args.repo)

    print()
    print_report(results)

    if args.json:
        out_path = PROJECT_ROOT / "benchmark_results.json"
        # Serialize results
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tests": results.tests,
        }
        out_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"\n  JSON results written to: {out_path}")


if __name__ == "__main__":
    main()
