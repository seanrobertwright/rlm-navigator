"""RLM Navigator Daemon — file watcher + TCP server + skeleton cache.

Cross-platform daemon that watches a project directory for changes,
maintains an in-memory cache of AST skeletons, and serves queries
over TCP JSON protocol on localhost.

Usage:
    python rlm_daemon.py --root <project_path> [--port 9177]
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from squeezer import squeeze, find_symbol, supported_languages, _detect_language, EXT_MAP
from rlm_repl import RLMRepl

DEFAULT_PORT = 9177
PORT_RANGE = (9177, 9196)  # Inclusive range for port scanning
IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
    "venv", ".env", "dist", "build", ".next", ".nuxt", "target",
    ".idea", ".vscode", ".DS_Store", ".rlm",
}
IGNORED_PATTERNS = {"*.pyc", "*.pyo", "*.class", "*.o", "*.so", "*.dll"}


# ---------------------------------------------------------------------------
# Lock file helpers — single-instance enforcement
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def write_lock_file(root: str, port: int) -> None:
    """Write daemon lock file to .rlm/daemon.lock."""
    lock_path = Path(root).resolve() / ".rlm" / "daemon.lock"
    if not lock_path.parent.is_dir():
        return
    lock_data = {
        "pid": os.getpid(),
        "port": port,
        "root": str(Path(root).resolve()),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    lock_path.write_text(json.dumps(lock_data))


def read_lock_file(root: str) -> Optional[dict]:
    """Read and parse lock file, or None if missing/corrupt."""
    lock_path = Path(root).resolve() / ".rlm" / "daemon.lock"
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def check_lock_file(root: str) -> Optional[dict]:
    """Check lock file. Returns lock data if held by alive process, else cleans up and returns None."""
    data = read_lock_file(root)
    if data is None:
        return None
    pid = data.get("pid")
    if pid and _is_pid_alive(pid):
        return data
    # Stale lock — clean up
    remove_lock_file(root)
    # Also clean stale port file
    port_path = Path(root).resolve() / ".rlm" / "port"
    if port_path.exists():
        try:
            port_path.unlink()
        except OSError:
            pass
    return None


def remove_lock_file(root: str) -> None:
    """Remove daemon lock file."""
    lock_path = Path(root).resolve() / ".rlm" / "daemon.lock"
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass


class SessionStats:
    """Tracks token savings across a daemon session."""

    def __init__(self):
        self._lock = threading.Lock()
        self.session_start = time.time()
        self.tool_calls = 0
        self.bytes_served = 0
        self.bytes_avoided = 0
        self.per_action: dict[str, dict] = {}
        self.progress_events: list[dict] = []
        self.progress_summary = {
            "sub_agent_dispatches": 0,
            "chunks_analyzed": 0,
            "answers_found": 0,
            "enrichments": 0,
            "analyses": 0,
        }

    def record(self, action: str, served_bytes: int, avoided_bytes: int = 0):
        with self._lock:
            self.tool_calls += 1
            self.bytes_served += served_bytes
            self.bytes_avoided += avoided_bytes
            if action not in self.per_action:
                self.per_action[action] = {"calls": 0, "bytes_served": 0, "bytes_avoided": 0}
            self.per_action[action]["calls"] += 1
            self.per_action[action]["bytes_served"] += served_bytes
            self.per_action[action]["bytes_avoided"] += avoided_bytes

    def record_progress(self, event: str, details: dict):
        with self._lock:
            entry = {"event": event, "details": details, "timestamp": time.time()}
            self.progress_events.append(entry)

            if event == "chunk_dispatch":
                self.progress_summary["sub_agent_dispatches"] += 1
                agent = details.get("agent", "")
                if agent == "rlm-enricher":
                    self.progress_summary["enrichments"] += 1
                else:
                    self.progress_summary["analyses"] += 1
            elif event == "chunk_complete":
                self.progress_summary["chunks_analyzed"] += 1
            elif event == "answer_found":
                self.progress_summary["answers_found"] += 1

    def to_dict(self) -> dict:
        with self._lock:
            total_bytes = self.bytes_served + self.bytes_avoided
            reduction_pct = round(self.bytes_avoided / total_bytes * 100) if total_bytes > 0 else 0
            breakdown = {}
            for action, data in self.per_action.items():
                entry: dict = {
                    "calls": data["calls"],
                    "tokens_served": data["bytes_served"] // 4,
                }
                if data["bytes_avoided"] > 0:
                    entry["tokens_avoided"] = data["bytes_avoided"] // 4
                breakdown[action] = entry
            result = {
                "tool_calls": self.tool_calls,
                "tokens_served": self.bytes_served // 4,
                "tokens_avoided": self.bytes_avoided // 4,
                "reduction_pct": reduction_pct,
                "duration_s": round(time.time() - self.session_start),
                "breakdown": breakdown,
                "progress_events": [dict(e) for e in self.progress_events],
                "progress_summary": dict(self.progress_summary),
            }
            if self.progress_events:
                result["progress_last_event"] = dict(self.progress_events[-1])
            return result


class SkeletonCache:
    """Thread-safe in-memory cache of squeezed file skeletons."""

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._mtimes: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, file_path: str) -> Optional[str]:
        """Get cached skeleton, or squeeze and cache it."""
        abs_path = str(Path(file_path).resolve())
        with self._lock:
            try:
                mtime = os.path.getmtime(abs_path)
            except OSError:
                return None

            cached = self._cache.get(abs_path)
            if cached is not None and self._mtimes.get(abs_path) == mtime:
                return cached

        # Squeeze outside the lock to avoid blocking
        skeleton = squeeze(abs_path)

        with self._lock:
            # Re-check mtime under lock to avoid caching stale data
            try:
                current_mtime = os.path.getmtime(abs_path)
            except OSError:
                return skeleton
            self._cache[abs_path] = skeleton
            self._mtimes[abs_path] = current_mtime
        return skeleton

    def invalidate(self, file_path: str):
        """Remove a file from cache."""
        abs_path = str(Path(file_path).resolve())
        with self._lock:
            self._cache.pop(abs_path, None)
            self._mtimes.pop(abs_path, None)

    def clear(self):
        """Clear the entire cache."""
        with self._lock:
            self._cache.clear()
            self._mtimes.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


class ChunkStore:
    """Disk-based file chunking store. Writes chunks to .rlm/chunks/ mirroring source tree."""

    def __init__(self, root: str, chunks_dir: Path, chunk_size: int = 200, overlap: int = 20):
        self.root = Path(root).resolve()
        self.chunks_dir = chunks_dir
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def _is_text_file(self, abs_path: Path) -> bool:
        """Check if file is text by trying to decode first 8KB as UTF-8."""
        try:
            with open(abs_path, "rb") as f:
                sample = f.read(8192)
            sample.decode("utf-8")
            return True
        except (OSError, UnicodeDecodeError):
            return False

    def _chunk_dir_for(self, abs_path: Path) -> Path:
        """Get the chunk directory for a given source file."""
        rel = abs_path.resolve().relative_to(self.root)
        return self.chunks_dir / rel

    def chunk_file(self, abs_path_str: str):
        """Chunk a single file to disk. Skips if mtime unchanged."""
        abs_path = Path(abs_path_str).resolve()
        if not abs_path.is_file():
            return
        if not self._is_text_file(abs_path):
            return

        chunk_dir = self._chunk_dir_for(abs_path)
        manifest_path = chunk_dir / "manifest.json"

        # Check mtime — skip if unchanged
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            return
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("mtime") == mtime:
                    return
            except (json.JSONDecodeError, OSError):
                pass

        # Read file lines
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return

        total_lines = len(lines)
        if total_lines == 0:
            return

        # Compute chunk boundaries (same logic as rlm_repl.py:chunk_indices)
        boundaries = []
        start = 1
        while start <= total_lines:
            end = min(start + self.chunk_size - 1, total_lines)
            boundaries.append((start, end))
            start = end + 1 - self.overlap
            if end == total_lines:
                break

        # Write chunks to temp dir then rename for atomicity
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(dir=self.chunks_dir))
        try:
            rel_path = abs_path.resolve().relative_to(self.root)
            for i, (s, e) in enumerate(boundaries):
                chunk_path = tmp_dir / f"chunk_{i:03d}.txt"
                header = f"# {str(rel_path).replace(os.sep, '/')} lines {s}-{e}\n"
                with open(chunk_path, "w", encoding="utf-8") as f:
                    f.write(header)
                    for line in lines[s - 1:e]:
                        f.write(line)

            # Write manifest
            manifest = {
                "total_chunks": len(boundaries),
                "chunk_size": self.chunk_size,
                "overlap": self.overlap,
                "total_lines": total_lines,
                "mtime": mtime,
            }
            (tmp_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            # Atomic swap: remove old dir, rename tmp
            import shutil
            if chunk_dir.exists():
                shutil.rmtree(chunk_dir)
            chunk_dir.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir.rename(chunk_dir)
        except (OSError, ValueError) as e:
            # Log and clean up temp on failure
            import logging
            logging.getLogger("rlm_daemon").warning("ChunkStore.chunk_file failed for %s: %s", abs_path_str, e)
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def remove_file(self, abs_path_str: str):
        """Remove chunk directory for a deleted file."""
        abs_path = Path(abs_path_str).resolve()
        chunk_dir = self._chunk_dir_for(abs_path)
        if chunk_dir.exists():
            import shutil
            shutil.rmtree(chunk_dir, ignore_errors=True)

    def get_manifest(self, abs_path_str: str) -> Optional[dict]:
        """Read and return manifest for a file, or None."""
        abs_path = Path(abs_path_str).resolve()
        manifest_path = self._chunk_dir_for(abs_path) / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def read_chunk(self, abs_path_str: str, chunk_index: int) -> Optional[str]:
        """Read a specific chunk file, or None."""
        abs_path = Path(abs_path_str).resolve()
        chunk_path = self._chunk_dir_for(abs_path) / f"chunk_{chunk_index:03d}.txt"
        if not chunk_path.exists():
            return None
        try:
            return chunk_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def scan_all(self):
        """Walk project tree and chunk all text files. Respects IGNORED_DIRS."""
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                self.chunk_file(fpath)


class RLMEventHandler(FileSystemEventHandler):
    """Watchdog handler that invalidates cache on file changes."""

    def __init__(self, cache: SkeletonCache, root: str, repl: Optional[RLMRepl] = None, chunk_store: Optional[ChunkStore] = None):
        self.cache = cache
        self.root = root
        self.repl = repl
        self.chunk_store = chunk_store

    def _should_ignore(self, path: str) -> bool:
        parts = Path(path).parts
        return any(part in IGNORED_DIRS for part in parts)

    def _notify_repl(self, file_path: str):
        if self.repl:
            self.repl.invalidate_dependencies(file_path)

    def _rechunk(self, file_path: str):
        if self.chunk_store:
            self.chunk_store.chunk_file(file_path)

    def _unchunk(self, file_path: str):
        if self.chunk_store:
            self.chunk_store.remove_file(file_path)

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self.cache.invalidate(event.src_path)
        self._notify_repl(event.src_path)
        self._rechunk(event.src_path)

    def on_created(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        # New file — will be lazily cached on next request
        self._rechunk(event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self.cache.invalidate(event.src_path)
        self._notify_repl(event.src_path)
        self._unchunk(event.src_path)

    def on_moved(self, event):
        if self._should_ignore(event.src_path):
            return
        self.cache.invalidate(event.src_path)
        self._notify_repl(event.src_path)
        self._unchunk(event.src_path)
        if hasattr(event, "dest_path"):
            self.cache.invalidate(event.dest_path)
            self._notify_repl(event.dest_path)
            if not self._should_ignore(event.dest_path):
                self._rechunk(event.dest_path)


def build_tree(dir_path: str, root: str, max_depth: int = 4) -> list[dict]:
    """Build a directory tree with file metadata.

    Returns a list of entries with type, name, path (relative), size, and language.
    """
    entries = []
    base = Path(dir_path)
    root_path = Path(root).resolve()

    if not base.exists():
        return entries

    try:
        items = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return entries

    for item in items:
        if item.name in IGNORED_DIRS or item.name.startswith("."):
            continue
        # Skip symlinks to prevent infinite loops
        if item.is_symlink():
            continue

        rel = str(item.resolve().relative_to(root_path)).replace("\\", "/")

        if item.is_dir():
            depth = len(Path(rel).parts)
            children = []
            if depth < max_depth:
                children = build_tree(str(item), root, max_depth)
            entries.append({
                "type": "dir",
                "name": item.name,
                "path": rel,
                "children": len(list(item.iterdir())) if depth >= max_depth else len(children),
                **({"entries": children} if depth < max_depth else {}),
            })
        else:
            lang = _detect_language(str(item))
            try:
                size = item.stat().st_size
            except OSError:
                size = 0
            entries.append({
                "type": "file",
                "name": item.name,
                "path": rel,
                "size": size,
                "language": lang,
            })

    return entries


def search_symbols(cache: SkeletonCache, root: str, query: str, dir_path: str) -> list[dict]:
    """Search for symbols matching query across files in a directory."""
    results = []
    base = Path(dir_path)
    if not base.exists():
        return results

    from doc_indexer import is_document_file

    for item in base.rglob("*"):
        if item.is_dir() or any(part in IGNORED_DIRS for part in item.parts):
            continue
        if _detect_language(str(item)) is None and not is_document_file(str(item)):
            continue

        skeleton = cache.get(str(item))
        if skeleton and query.lower() in skeleton.lower():
            root_path = Path(root).resolve()
            rel = str(item.resolve().relative_to(root_path)).replace("\\", "/")
            # Extract matching lines from skeleton
            matches = []
            for line in skeleton.split("\n"):
                if query.lower() in line.lower():
                    matches.append(line.strip())
            results.append({
                "path": rel,
                "matches": matches[:10],  # Cap matches per file
            })

        if len(results) >= 50:  # Cap total results
            break

    return results


def _find_section(node: dict, title: str) -> Optional[dict]:
    """Recursively find a section node by title (case-insensitive)."""
    if node.get("name", "").lower() == title.lower():
        return node
    for child in node.get("children", []):
        found = _find_section(child, title)
        if found:
            return found
    return None


def handle_request(data: bytes, cache: SkeletonCache, root: str, repl: RLMRepl = None, stats: SessionStats = None, chunk_store: ChunkStore = None, shutdown_event: threading.Event = None) -> bytes:
    """Process a JSON request and return a JSON response."""
    response = _handle_request_inner(data, cache, root, repl, stats, chunk_store, shutdown_event)
    # Inject session stats into every successful JSON response
    if stats:
        try:
            resp_dict = json.loads(response.decode("utf-8"))
            if "error" not in resp_dict:
                s = stats.to_dict()
                resp_dict["_stats"] = {
                    "tokens_served": s["tokens_served"],
                    "tokens_avoided": s["tokens_avoided"],
                    "reduction_pct": s["reduction_pct"],
                    "tool_calls": s["tool_calls"],
                }
            return json.dumps(resp_dict).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return response


def _handle_request_inner(data: bytes, cache: SkeletonCache, root: str, repl: RLMRepl = None, stats: SessionStats = None, chunk_store: ChunkStore = None, shutdown_event: threading.Event = None) -> bytes:
    """Process a JSON request and return a JSON response."""
    try:
        req = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json.dumps({"error": "Invalid JSON"}).encode("utf-8")

    action = req.get("action", "")
    root_path = Path(root).resolve()

    if action == "squeeze":
        path = req.get("path", "")
        abs_path = str((root_path / path).resolve())
        # Security: ensure path is within root
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        skeleton = cache.get(abs_path)
        if skeleton is None:
            return json.dumps({"error": f"File not found: {path}"}).encode("utf-8")
        response = json.dumps({"skeleton": skeleton}).encode("utf-8")
        if stats:
            try:
                full_size = os.path.getsize(abs_path)
            except OSError:
                full_size = len(response)
            stats.record("squeeze", len(response), max(0, full_size - len(skeleton.encode("utf-8"))))
        return response

    elif action == "find":
        path = req.get("path", "")
        symbol = req.get("symbol", "")
        abs_path = str((root_path / path).resolve())
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        result = find_symbol(abs_path, symbol)
        if result:
            response = json.dumps({"start_line": result[0], "end_line": result[1]}).encode("utf-8")
            if stats:
                try:
                    full_size = os.path.getsize(abs_path)
                    # Estimate drilled lines size from line range
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    drilled = "".join(lines[result[0] - 1:result[1]])
                    drilled_size = len(drilled.encode("utf-8"))
                except (OSError, IndexError):
                    full_size = len(response)
                    drilled_size = len(response)
                stats.record("find", len(response) + drilled_size, max(0, full_size - drilled_size))
            return response
        return json.dumps({"error": f"Symbol '{symbol}' not found in {path}"}).encode("utf-8")

    elif action == "tree":
        path = req.get("path", "")
        max_depth = req.get("max_depth", 4)
        abs_path = str((root_path / path).resolve()) if path else str(root_path)
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        tree = build_tree(abs_path, root, max_depth)
        response = json.dumps({"tree": tree}).encode("utf-8")
        if stats:
            stats.record("tree", len(response))
        return response

    elif action == "search":
        query = req.get("query", "")
        path = req.get("path", "")
        abs_path = str((root_path / path).resolve()) if path else str(root_path)
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        results = search_symbols(cache, root, query, abs_path)
        response = json.dumps({"results": results}).encode("utf-8")
        if stats:
            # Sum full file sizes of matched files
            sum_file_sizes = 0
            for r in results:
                try:
                    sum_file_sizes += os.path.getsize(str(root_path / r["path"]))
                except OSError:
                    pass
            stats.record("search", len(response), max(0, sum_file_sizes - len(response)))
        return response

    elif action == "status":
        from config import RLMConfig
        cfg = RLMConfig()
        resp = {
            "status": "alive",
            "root": root,
            "cache_size": cache.size,
            "languages": supported_languages(),
            "enrichment_available": cfg.enrichment_enabled,
            "doc_indexing_available": cfg.doc_indexing_enabled,
        }
        if stats:
            resp["session"] = stats.to_dict()
        return json.dumps(resp).encode("utf-8")

    elif action == "repl_init":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        result = repl.init()
        return json.dumps(result).encode("utf-8")

    elif action == "repl_exec":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        code = req.get("code", "")
        timeout = req.get("timeout", 30)
        result = repl.exec(code, timeout=timeout)
        return json.dumps(result).encode("utf-8")

    elif action == "repl_status":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        result = repl.status()
        return json.dumps(result).encode("utf-8")

    elif action == "repl_reset":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        result = repl.reset()
        return json.dumps(result).encode("utf-8")

    elif action == "repl_export_buffers":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        result = repl.export_buffers()
        return json.dumps(result).encode("utf-8")

    elif action == "chunks_list":
        if chunk_store is None:
            return json.dumps({"error": "Chunk store not available"}).encode("utf-8")
        path = req.get("path", "")
        abs_path = str((root_path / path).resolve())
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        manifest = chunk_store.get_manifest(abs_path)
        if manifest is None:
            return json.dumps({"status": "pending"}).encode("utf-8")
        response = json.dumps({"manifest": manifest, "status": "ready"}).encode("utf-8")
        if stats:
            stats.record("chunks_list", len(response))
        return response

    elif action == "chunks_read":
        if chunk_store is None:
            return json.dumps({"error": "Chunk store not available"}).encode("utf-8")
        path = req.get("path", "")
        chunk_idx = req.get("chunk", 0)
        abs_path = str((root_path / path).resolve())
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        manifest = chunk_store.get_manifest(abs_path)
        if manifest is None:
            return json.dumps({"error": f"No chunks for: {path}"}).encode("utf-8")
        content = chunk_store.read_chunk(abs_path, chunk_idx)
        if content is None:
            return json.dumps({"error": f"Chunk {chunk_idx} not found for: {path}"}).encode("utf-8")
        # Compute line range from chunk index and manifest params
        total_lines = manifest["total_lines"]
        csize = manifest["chunk_size"]
        coverlap = manifest["overlap"]
        s = 1
        for i in range(chunk_idx):
            e = min(s + csize - 1, total_lines)
            s = e + 1 - coverlap
        end_line = min(s + csize - 1, total_lines)
        response = json.dumps({
            "content": content,
            "chunk": chunk_idx,
            "total_chunks": manifest["total_chunks"],
            "lines": f"{s}-{end_line}",
        }).encode("utf-8")
        if stats:
            try:
                full_size = os.path.getsize(abs_path)
            except OSError:
                full_size = len(response)
            stats.record("chunks_read", len(response), max(0, full_size - len(content.encode("utf-8"))))
        return response

    elif action == "doc_map":
        rel = req.get("path", "")
        abs_path = str((root_path / rel).resolve())
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        if not Path(abs_path).exists():
            return json.dumps({"error": f"File not found: {rel}"}).encode("utf-8")

        from doc_indexer import is_document_file, index_document
        from config import RLMConfig
        if not is_document_file(abs_path):
            return json.dumps({"error": f"Not a document file: {rel}"}).encode("utf-8")

        cfg = RLMConfig()
        tree = index_document(abs_path, cfg)
        if tree is None:
            return json.dumps({"error": f"Failed to index: {rel}"}).encode("utf-8")

        response = json.dumps({"tree": tree}).encode("utf-8")
        if stats:
            stats.record("doc_map", len(response))
        return response

    elif action == "doc_drill":
        rel = req.get("path", "")
        section = req.get("section", "")
        abs_path = str((root_path / rel).resolve())
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        if not Path(abs_path).exists():
            return json.dumps({"error": f"File not found: {rel}"}).encode("utf-8")

        from doc_indexer import index_document
        from config import RLMConfig
        cfg = RLMConfig()
        tree = index_document(abs_path, cfg)
        if not tree:
            return json.dumps({"error": "Failed to index document"}).encode("utf-8")

        node = _find_section(tree, section)
        if not node:
            return json.dumps({"error": f"Section not found: {section}"}).encode("utf-8")

        r = node.get("range")
        if r:
            lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").split("\n")
            content = "\n".join(lines[r["start"]-1:r["end"]])
        else:
            content = f"[Section '{section}' found but no line range available]"

        response = json.dumps({"content": content}).encode("utf-8")
        if stats:
            try:
                full_size = os.path.getsize(abs_path)
            except OSError:
                full_size = len(response)
            stats.record("doc_drill", len(response), max(0, full_size - len(content.encode("utf-8"))))
        return response

    elif action == "assess":
        query = req.get("query", "")
        context_summary = req.get("context_summary", "")
        assessment = (
            f"Query: {query}\n"
            f"Context gathered: {context_summary}\n\n"
            f"Assessment: Review the context above. If it directly addresses the query, "
            f"synthesize your answer. If gaps remain, continue navigating."
        )
        response = json.dumps({"assessment": assessment}).encode("utf-8")
        if stats:
            stats.record("assess", len(response))
        return response

    elif action == "progress":
        event = req.get("event", "")
        details = req.get("details", {})
        valid_events = {
            "chunking_start", "chunking_complete", "chunk_dispatch",
            "chunk_complete", "synthesis_start", "synthesis_complete",
            "answer_found", "queries_suggested",
        }
        if not event:
            return json.dumps({"error": "Missing 'event' field"}).encode("utf-8")
        if event not in valid_events:
            return json.dumps({"error": f"Invalid event: {event}. Valid: {sorted(valid_events)}"}).encode("utf-8")
        if stats:
            stats.record_progress(event, details)
        return json.dumps({"ok": True}).encode("utf-8")

    elif action == "shutdown":
        if shutdown_event is None:
            return json.dumps({"error": "Shutdown not available"}).encode("utf-8")
        shutdown_event.set()
        return json.dumps({"status": "shutting_down"}).encode("utf-8")

    else:
        return json.dumps({"error": f"Unknown action: {action}"}).encode("utf-8")


def handle_client(conn: socket.socket, cache: SkeletonCache, root: str, repl: RLMRepl = None, stats: SessionStats = None, chunk_store: ChunkStore = None, shutdown_event: threading.Event = None):
    """Handle a single TCP client connection."""
    try:
        # Read data with a simple length-prefix or newline protocol
        data = b""
        conn.settimeout(5.0)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            # Try to parse — if valid JSON, we're done
            try:
                json.loads(data)
                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        if not data:
            # Health check — bare connection
            conn.sendall(b"ALIVE")
            return

        response = handle_request(data, cache, root, repl, stats, chunk_store, shutdown_event)
        conn.sendall(response)
    except socket.timeout:
        # Bare connection for health check
        if not data:
            conn.sendall(b"ALIVE")
    except Exception as e:
        try:
            conn.sendall(json.dumps({"error": str(e)}).encode("utf-8"))
        except Exception:
            pass
    finally:
        conn.close()


def run_server(root: str, port: int, idle_timeout: int = 300):
    """Start the TCP server and file watcher."""
    cache = SkeletonCache()
    root_path = str(Path(root).resolve())

    # Check for existing daemon (lock file guard)
    rlm_dir = Path(root_path) / ".rlm"
    if rlm_dir.is_dir():
        existing = check_lock_file(root_path)
        if existing:
            print(f"Error: Daemon already running (PID {existing['pid']}, port {existing['port']})", file=sys.stderr)
            sys.exit(1)

    repl = RLMRepl(root_path)
    stats = SessionStats()

    # Idle tracking for auto-shutdown
    last_activity = time.time()
    activity_lock = threading.Lock()
    shutdown_event = threading.Event()

    def update_activity():
        nonlocal last_activity
        with activity_lock:
            last_activity = time.time()

    def idle_watchdog():
        poll_interval = min(60, max(1, idle_timeout // 2))
        while not shutdown_event.is_set():
            shutdown_event.wait(poll_interval)
            if shutdown_event.is_set():
                break
            with activity_lock:
                idle_secs = time.time() - last_activity
            if idle_secs > idle_timeout:
                print(f"\nIdle timeout ({idle_timeout}s) reached — shutting down.")
                shutdown_event.set()
                break

    # Create ChunkStore if .rlm/ exists (npx install mode)
    chunk_store = None
    rlm_dir = Path(root_path) / ".rlm"
    if rlm_dir.is_dir():
        chunk_store = ChunkStore(root_path, rlm_dir / "chunks")
        # Run initial scan in background thread
        scan_thread = threading.Thread(target=chunk_store.scan_all, daemon=True)
        scan_thread.start()

    # Start file watcher
    handler = RLMEventHandler(cache, root_path, repl=repl, chunk_store=chunk_store)
    observer = Observer()
    observer.schedule(handler, root_path, recursive=True)
    observer.start()

    # Start TCP server — scan ports if requested port is busy
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bound_port = None
    max_port = PORT_RANGE[1] - PORT_RANGE[0] + 1
    for try_port in range(port, port + max_port):
        try:
            server.bind(("127.0.0.1", try_port))
            bound_port = try_port
            break
        except OSError:
            continue
    if bound_port is None:
        print(f"Error: Could not bind to any port in range {port}-{port + max_port - 1}", file=sys.stderr)
        observer.stop()
        observer.join()
        sys.exit(1)
    server.listen(5)
    server.settimeout(1.0)  # Allow accept() to be interrupted for shutdown

    # Write port file if .rlm/ directory exists (npx install mode)
    port_file = Path(root_path) / ".rlm" / "port"
    if port_file.parent.is_dir():
        port_file.write_text(json.dumps({"port": bound_port, "pid": os.getpid()}))

    # Write lock file
    write_lock_file(root_path, bound_port)

    # Start idle watchdog if timeout is enabled
    if idle_timeout > 0:
        watchdog_thread = threading.Thread(target=idle_watchdog, daemon=True)
        watchdog_thread.start()

    print(f"RLM Daemon active — watching: {root_path}")
    print(f"TCP server listening on 127.0.0.1:{bound_port}")
    print(f"Languages available: {', '.join(supported_languages())}")
    if idle_timeout > 0:
        print(f"Idle timeout: {idle_timeout}s")

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            update_activity()
            thread = threading.Thread(
                target=handle_client,
                args=(conn, cache, root_path, repl, stats, chunk_store, shutdown_event),
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        # Persist session stats before cleanup
        if stats and stats.tool_calls > 0:
            log_file = Path(root_path) / ".rlm" / "sessions.jsonl"
            if log_file.parent.is_dir():
                try:
                    entry = stats.to_dict()
                    entry["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    entry["root"] = root_path
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")
                except OSError:
                    pass

        if port_file.exists():
            try:
                port_file.unlink()
            except OSError:
                pass
        remove_lock_file(root_path)
        observer.stop()
        observer.join()
        server.close()


def main():
    parser = argparse.ArgumentParser(description="RLM Navigator Daemon")
    parser.add_argument("--root", required=True, help="Project root directory to watch")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default: {DEFAULT_PORT})")
    parser.add_argument("--idle-timeout", type=int, default=300,
                        help="Seconds of inactivity before auto-shutdown (0 = disabled, default: 300)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Error: {args.root} is not a directory", file=sys.stderr)
        sys.exit(1)

    run_server(str(root), args.port, args.idle_timeout)


if __name__ == "__main__":
    main()
