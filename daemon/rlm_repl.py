"""RLM Stateful REPL — pickle-persisted Python execution environment.

Provides a stateful Python REPL with helpers for codebase analysis:
peek, grep, chunk_indices, write_chunks, add_buffer.

State persists across calls via pickle at <root>/.claude/rlm_state/state.pkl.

Usage:
    python rlm_repl.py --root <path> [--exec "code"] [--status] [--reset] [--export-buffers]
"""

import argparse
import io
import json
import os
import pickle
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional


MAX_OUTPUT_CHARS = 8000


class _DependencyTracker:
    """Tracks file accesses during a single exec() call."""

    def __init__(self, root: str):
        self._root = root
        self._files: dict[str, float] = {}

    def track_file(self, rel_path: str, abs_path: str):
        """Record a file access with its current mtime."""
        try:
            mtime = os.path.getmtime(abs_path)
        except OSError:
            mtime = 0.0
        self._files[rel_path] = mtime

    def get_tracked(self) -> dict[str, float]:
        return dict(self._files)


class RLMRepl:
    """Stateful Python REPL with pickle persistence and codebase helpers."""

    def __init__(self, root: str, state_dir: Optional[str] = None):
        self.root = str(Path(root).resolve())
        self.state_dir = state_dir or os.path.join(self.root, ".claude", "rlm_state")
        self.state_path = os.path.join(self.state_dir, "state.pkl")
        self._lock = threading.Lock()
        self._namespace: dict = {}
        self._load_state()

    def _load_state(self):
        """Load persisted state from pickle, inject helpers."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "rb") as f:
                    self._namespace = pickle.load(f)
            except Exception:
                self._namespace = {}
        self._ensure_meta()
        self._inject_helpers()

    def _ensure_meta(self):
        """Ensure metadata keys exist in namespace."""
        if "_rlm_buffers_" not in self._namespace:
            self._namespace["_rlm_buffers_"] = {}
        if "_rlm_meta_" not in self._namespace:
            self._namespace["_rlm_meta_"] = {"exec_count": 0, "last_exec": None}
        if "_rlm_deps_" not in self._namespace:
            self._namespace["_rlm_deps_"] = {"variables": {}, "buffers": {}}

    def _inject_helpers(self):
        """Inject codebase helper functions into the namespace."""
        root = self.root
        ns = self._namespace

        def _get_tracker() -> Optional[_DependencyTracker]:
            return ns.get("_rlm_tracker_")

        def peek(file_path: str, start: int = 1, end: Optional[int] = None) -> str:
            """Read lines from a file relative to project root."""
            abs_path = os.path.join(root, file_path)
            if not os.path.isfile(abs_path):
                return f"Error: file not found: {file_path}"
            tracker = _get_tracker()
            if tracker:
                rel = file_path.replace("\\", "/")
                tracker.track_file(rel, abs_path)
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if end is None:
                end = len(lines)
            start = max(1, start)
            end = min(end, len(lines))
            selected = lines[start - 1:end]
            return "".join(
                f"{start + i:4d} | {line}" for i, line in enumerate(selected)
            )

        def grep(pattern: str, path: str = ".", max_results: int = 50) -> str:
            """Regex search across files, return file:line:content."""
            search_root = os.path.join(root, path)
            if not os.path.exists(search_root):
                return f"Error: path not found: {path}"
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return f"Error: invalid regex: {e}"
            tracker = _get_tracker()
            results = []
            for dirpath, dirnames, filenames in os.walk(search_root):
                # Skip ignored directories
                dirnames[:] = [
                    d for d in dirnames
                    if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"}
                ]
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    rel = os.path.relpath(fpath, root).replace("\\", "/")
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            for i, line in enumerate(f, 1):
                                if regex.search(line):
                                    if tracker:
                                        tracker.track_file(rel, fpath)
                                    results.append(f"{rel}:{i}:{line.rstrip()}")
                                    if len(results) >= max_results:
                                        return "\n".join(results)
                    except (OSError, UnicodeDecodeError):
                        continue
            return "\n".join(results) if results else "No matches found"

        def chunk_indices(file_path: str, size: int = 200, overlap: int = 20) -> list:
            """Compute (start_line, end_line) tuples for chunking a file."""
            abs_path = os.path.join(root, file_path)
            if not os.path.isfile(abs_path):
                return []
            tracker = _get_tracker()
            if tracker:
                rel = file_path.replace("\\", "/")
                tracker.track_file(rel, abs_path)
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                total = sum(1 for _ in f)
            chunks = []
            start = 1
            while start <= total:
                end = min(start + size - 1, total)
                chunks.append((start, end))
                start = end + 1 - overlap
                if end == total:
                    break
            return chunks

        def write_chunks(
            file_path: str,
            out_dir: Optional[str] = None,
            size: int = 200,
            overlap: int = 20,
        ) -> list:
            """Write file chunks to .claude/rlm_state/chunks/, return list of paths."""
            abs_path = os.path.join(root, file_path)
            if not os.path.isfile(abs_path):
                return []
            tracker = _get_tracker()
            if tracker:
                rel = file_path.replace("\\", "/")
                tracker.track_file(rel, abs_path)
            if out_dir is None:
                out_dir = os.path.join(root, ".claude", "rlm_state", "chunks")
            os.makedirs(out_dir, exist_ok=True)

            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            indices = chunk_indices(file_path, size, overlap)
            paths = []
            base_name = Path(file_path).stem
            for i, (start, end) in enumerate(indices):
                chunk_path = os.path.join(out_dir, f"{base_name}_chunk_{i}.txt")
                with open(chunk_path, "w", encoding="utf-8") as f:
                    header = f"# {file_path} lines {start}-{end}\n"
                    f.write(header)
                    for line in lines[start - 1:end]:
                        f.write(line)
                paths.append(chunk_path)
            return paths

        def add_buffer(key: str, text: str):
            """Append text to a named buffer list in REPL state."""
            buffers = ns.get("_rlm_buffers_", {})
            if key not in buffers:
                buffers[key] = []
            buffers[key].append(text)
            ns["_rlm_buffers_"] = buffers
            # Merge tracked files into buffer deps
            tracker = _get_tracker()
            if tracker:
                deps = ns.get("_rlm_deps_", {"variables": {}, "buffers": {}})
                buf_deps = deps["buffers"].get(key, {"files": {}})
                buf_deps["files"].update(tracker.get_tracked())
                deps["buffers"][key] = buf_deps
                ns["_rlm_deps_"] = deps

        self._namespace["peek"] = peek
        self._namespace["grep"] = grep
        self._namespace["chunk_indices"] = chunk_indices
        self._namespace["write_chunks"] = write_chunks
        self._namespace["add_buffer"] = add_buffer

    def _save_state(self):
        """Persist pickle-safe variables to disk."""
        os.makedirs(self.state_dir, exist_ok=True)
        safe = {}
        for k, v in self._namespace.items():
            if k in ("peek", "grep", "chunk_indices", "write_chunks", "add_buffer", "_rlm_tracker_"):
                continue
            try:
                pickle.dumps(v)
                safe[k] = v
            except Exception:
                continue
        with open(self.state_path, "wb") as f:
            pickle.dump(safe, f)

    def init(self) -> dict:
        """Initialize fresh REPL state."""
        with self._lock:
            os.makedirs(self.state_dir, exist_ok=True)
            self._namespace = {}
            self._ensure_meta()
            self._inject_helpers()
            self._save_state()
            return {"success": True}

    def exec(self, code: str) -> dict:
        """Execute Python code in the REPL namespace."""
        with self._lock:
            # Create fresh tracker and snapshot existing vars
            tracker = _DependencyTracker(self.root)
            self._namespace["_rlm_tracker_"] = tracker
            vars_before = set(self._namespace.keys())

            stdout_capture = io.StringIO()
            old_stdout = sys.stdout
            error = None
            try:
                sys.stdout = stdout_capture
                exec(code, self._namespace)
            except Exception:
                error = traceback.format_exc()
            finally:
                sys.stdout = old_stdout

            output = stdout_capture.getvalue()
            if len(output) > MAX_OUTPUT_CHARS:
                remaining = len(output) - MAX_OUTPUT_CHARS
                tokens_est = remaining // 4
                output = output[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {remaining} more chars, ~{tokens_est} tokens)"

            # Merge tracked files into deps for new/modified variables
            tracked = tracker.get_tracked()
            if tracked:
                deps = self._namespace.get("_rlm_deps_", {"variables": {}, "buffers": {}})
                vars_after = set(self._namespace.keys())
                new_or_modified = (vars_after - vars_before) | {
                    k for k in vars_after
                    if not k.startswith("_") and k not in (
                        "peek", "grep", "chunk_indices", "write_chunks", "add_buffer"
                    )
                    and k in vars_before
                }
                for var_name in new_or_modified:
                    if var_name.startswith("_") or var_name in (
                        "peek", "grep", "chunk_indices", "write_chunks", "add_buffer"
                    ):
                        continue
                    var_deps = deps["variables"].get(var_name, {"files": {}})
                    var_deps["files"].update(tracked)
                    deps["variables"][var_name] = var_deps
                self._namespace["_rlm_deps_"] = deps

            # Clean up tracker
            self._namespace.pop("_rlm_tracker_", None)

            meta = self._namespace.get("_rlm_meta_", {})
            meta["exec_count"] = meta.get("exec_count", 0) + 1
            meta["last_exec"] = time.time()
            self._namespace["_rlm_meta_"] = meta

            self._save_state()

            # Compute user-visible variables
            variables = [
                k for k in self._namespace
                if not k.startswith("_") and k not in (
                    "peek", "grep", "chunk_indices", "write_chunks", "add_buffer"
                )
            ]

            result = {
                "success": error is None,
                "output": output,
                "variables": variables,
            }
            if error:
                result["error"] = error

            # Check staleness
            staleness = self._check_staleness()
            if staleness:
                result["staleness_warning"] = staleness

            return result

    def status(self) -> dict:
        """Return current REPL state info."""
        with self._lock:
            variables = [
                k for k in self._namespace
                if not k.startswith("_") and k not in (
                    "peek", "grep", "chunk_indices", "write_chunks", "add_buffer"
                )
            ]
            buffers = self._namespace.get("_rlm_buffers_", {})
            meta = self._namespace.get("_rlm_meta_", {})
            result = {
                "variables": variables,
                "buffer_count": {k: len(v) for k, v in buffers.items()},
                "exec_count": meta.get("exec_count", 0),
            }
            staleness = self._check_staleness()
            if staleness:
                result["staleness"] = staleness
            return result

    def reset(self) -> dict:
        """Delete all state and start fresh."""
        with self._lock:
            if os.path.exists(self.state_path):
                os.remove(self.state_path)
            self._namespace = {}
            self._ensure_meta()
            self._inject_helpers()
            return {"success": True}

    def _check_staleness(self) -> Optional[dict]:
        """Compare stored mtimes vs current. Return stale info or None."""
        deps = self._namespace.get("_rlm_deps_", {"variables": {}, "buffers": {}})
        stale_vars = {}
        stale_buffers = {}

        for var_name, var_info in deps.get("variables", {}).items():
            stale_files = []
            for rel_path, stored_mtime in var_info.get("files", {}).items():
                abs_path = os.path.join(self.root, rel_path)
                if not os.path.exists(abs_path):
                    stale_files.append({"file": rel_path, "reason": "deleted"})
                else:
                    try:
                        current_mtime = os.path.getmtime(abs_path)
                        if current_mtime != stored_mtime:
                            stale_files.append({"file": rel_path, "reason": "modified"})
                    except OSError:
                        stale_files.append({"file": rel_path, "reason": "inaccessible"})
            if stale_files:
                stale_vars[var_name] = stale_files

        for buf_name, buf_info in deps.get("buffers", {}).items():
            stale_files = []
            for rel_path, stored_mtime in buf_info.get("files", {}).items():
                abs_path = os.path.join(self.root, rel_path)
                if not os.path.exists(abs_path):
                    stale_files.append({"file": rel_path, "reason": "deleted"})
                else:
                    try:
                        current_mtime = os.path.getmtime(abs_path)
                        if current_mtime != stored_mtime:
                            stale_files.append({"file": rel_path, "reason": "modified"})
                    except OSError:
                        stale_files.append({"file": rel_path, "reason": "inaccessible"})
            if stale_files:
                stale_buffers[buf_name] = stale_files

        if not stale_vars and not stale_buffers:
            return None
        result = {}
        if stale_vars:
            result["variables"] = stale_vars
        if stale_buffers:
            result["buffers"] = stale_buffers
        return result

    def invalidate_dependencies(self, file_path: str) -> list[str]:
        """Mark deps stale for a changed file. Returns affected var/buffer names."""
        abs_path = str(Path(file_path).resolve())
        rel_path = os.path.relpath(abs_path, self.root).replace("\\", "/")
        affected = []
        with self._lock:
            deps = self._namespace.get("_rlm_deps_", {"variables": {}, "buffers": {}})
            for var_name, var_info in deps.get("variables", {}).items():
                if rel_path in var_info.get("files", {}):
                    affected.append(f"var:{var_name}")
            for buf_name, buf_info in deps.get("buffers", {}).items():
                if rel_path in buf_info.get("files", {}):
                    affected.append(f"buffer:{buf_name}")
        return affected

    def export_buffers(self) -> dict:
        """Export all accumulated buffers."""
        with self._lock:
            buffers = self._namespace.get("_rlm_buffers_", {})
            return {"buffers": dict(buffers)}


def main():
    parser = argparse.ArgumentParser(description="RLM Stateful REPL")
    parser.add_argument("--root", required=True, help="Project root directory")
    parser.add_argument("--exec", dest="code", help="Execute code")
    parser.add_argument("--status", action="store_true", help="Show REPL status")
    parser.add_argument("--reset", action="store_true", help="Reset REPL state")
    parser.add_argument("--export-buffers", action="store_true", help="Export buffers")
    args = parser.parse_args()

    repl = RLMRepl(args.root)

    if args.reset:
        print(json.dumps(repl.reset(), indent=2))
    elif args.status:
        print(json.dumps(repl.status(), indent=2))
    elif args.export_buffers:
        print(json.dumps(repl.export_buffers(), indent=2))
    elif args.code:
        result = repl.exec(args.code)
        print(json.dumps(result, indent=2))
    else:
        # Interactive REPL mode
        repl.init()
        print("RLM REPL — type Python code, 'quit' to exit")
        print(f"Root: {repl.root}")
        print("Helpers: peek(), grep(), chunk_indices(), write_chunks(), add_buffer()")
        while True:
            try:
                code = input(">>> ")
            except (EOFError, KeyboardInterrupt):
                break
            if code.strip() in ("quit", "exit"):
                break
            if not code.strip():
                continue
            result = repl.exec(code)
            if result.get("output"):
                print(result["output"], end="")
            if result.get("error"):
                print(result["error"], end="")


if __name__ == "__main__":
    main()
