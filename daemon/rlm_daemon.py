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
IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
    "venv", ".env", "dist", "build", ".next", ".nuxt", "target",
    ".idea", ".vscode", ".DS_Store", ".rlm",
}
IGNORED_PATTERNS = {"*.pyc", "*.pyo", "*.class", "*.o", "*.so", "*.dll"}


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

            if abs_path in self._cache and self._mtimes.get(abs_path) == mtime:
                return self._cache[abs_path]

        # Squeeze outside the lock to avoid blocking
        skeleton = squeeze(abs_path)

        with self._lock:
            self._cache[abs_path] = skeleton
            self._mtimes[abs_path] = mtime
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


class RLMEventHandler(FileSystemEventHandler):
    """Watchdog handler that invalidates cache on file changes."""

    def __init__(self, cache: SkeletonCache, root: str, repl: Optional[RLMRepl] = None):
        self.cache = cache
        self.root = root
        self.repl = repl

    def _should_ignore(self, path: str) -> bool:
        parts = Path(path).parts
        return any(part in IGNORED_DIRS for part in parts)

    def _notify_repl(self, file_path: str):
        if self.repl:
            self.repl.invalidate_dependencies(file_path)

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self.cache.invalidate(event.src_path)
        self._notify_repl(event.src_path)

    def on_created(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        # New file — will be lazily cached on next request

    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self.cache.invalidate(event.src_path)
        self._notify_repl(event.src_path)

    def on_moved(self, event):
        if self._should_ignore(event.src_path):
            return
        self.cache.invalidate(event.src_path)
        self._notify_repl(event.src_path)
        if hasattr(event, "dest_path"):
            self.cache.invalidate(event.dest_path)
            self._notify_repl(event.dest_path)


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

    for item in base.rglob("*"):
        if item.is_dir() or any(part in IGNORED_DIRS for part in item.parts):
            continue
        if _detect_language(str(item)) is None:
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


def handle_request(data: bytes, cache: SkeletonCache, root: str, repl: RLMRepl = None) -> bytes:
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
        return json.dumps({"skeleton": skeleton}).encode("utf-8")

    elif action == "find":
        path = req.get("path", "")
        symbol = req.get("symbol", "")
        abs_path = str((root_path / path).resolve())
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        result = find_symbol(abs_path, symbol)
        if result:
            return json.dumps({"start_line": result[0], "end_line": result[1]}).encode("utf-8")
        return json.dumps({"error": f"Symbol '{symbol}' not found in {path}"}).encode("utf-8")

    elif action == "tree":
        path = req.get("path", "")
        max_depth = req.get("max_depth", 4)
        abs_path = str((root_path / path).resolve()) if path else str(root_path)
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        tree = build_tree(abs_path, root, max_depth)
        return json.dumps({"tree": tree}).encode("utf-8")

    elif action == "search":
        query = req.get("query", "")
        path = req.get("path", "")
        abs_path = str((root_path / path).resolve()) if path else str(root_path)
        if not abs_path.startswith(str(root_path)):
            return json.dumps({"error": "Path outside project root"}).encode("utf-8")
        results = search_symbols(cache, root, query, abs_path)
        return json.dumps({"results": results}).encode("utf-8")

    elif action == "status":
        return json.dumps({
            "status": "alive",
            "root": root,
            "cache_size": cache.size,
            "languages": supported_languages(),
        }).encode("utf-8")

    elif action == "repl_init":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        result = repl.init()
        return json.dumps(result).encode("utf-8")

    elif action == "repl_exec":
        if repl is None:
            return json.dumps({"error": "REPL not available"}).encode("utf-8")
        code = req.get("code", "")
        result = repl.exec(code)
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

    else:
        return json.dumps({"error": f"Unknown action: {action}"}).encode("utf-8")


def handle_client(conn: socket.socket, cache: SkeletonCache, root: str, repl: RLMRepl = None):
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
            except json.JSONDecodeError:
                continue

        if not data:
            # Health check — bare connection
            conn.sendall(b"ALIVE")
            return

        response = handle_request(data, cache, root, repl)
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


def run_server(root: str, port: int):
    """Start the TCP server and file watcher."""
    cache = SkeletonCache()
    root_path = str(Path(root).resolve())
    repl = RLMRepl(root_path)

    # Start file watcher
    handler = RLMEventHandler(cache, root_path, repl=repl)
    observer = Observer()
    observer.schedule(handler, root_path, recursive=True)
    observer.start()

    # Start TCP server — scan ports if requested port is busy
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bound_port = None
    for try_port in range(port, port + 20):
        try:
            server.bind(("127.0.0.1", try_port))
            bound_port = try_port
            break
        except OSError:
            continue
    if bound_port is None:
        print(f"Error: Could not bind to any port in range {port}-{port + 19}", file=sys.stderr)
        observer.stop()
        observer.join()
        sys.exit(1)
    server.listen(5)

    # Write port file if .rlm/ directory exists (npx install mode)
    port_file = Path(root_path) / ".rlm" / "port"
    if port_file.parent.is_dir():
        port_file.write_text(str(bound_port))

    print(f"RLM Daemon active — watching: {root_path}")
    print(f"TCP server listening on 127.0.0.1:{bound_port}")
    print(f"Languages available: {', '.join(supported_languages())}")

    try:
        while True:
            conn, addr = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(conn, cache, root_path, repl),
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if port_file.exists():
            try:
                port_file.unlink()
            except OSError:
                pass
        observer.stop()
        observer.join()
        server.close()


def main():
    parser = argparse.ArgumentParser(description="RLM Navigator Daemon")
    parser.add_argument("--root", required=True, help="Project root directory to watch")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Error: {args.root} is not a directory", file=sys.stderr)
        sys.exit(1)

    run_server(str(root), args.port)


if __name__ == "__main__":
    main()
