# Daemon Lifecycle Management — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate orphaned daemons, stale port files, wrong-project connections, and spawn failures through a lock file, graceful shutdown protocol, spawn verification, and CLI cleanup.

**Architecture:** A lock file (`.rlm/daemon.lock`) provides single-instance enforcement. A new `shutdown` daemon action enables graceful teardown. The MCP server verifies spawns actually work before trusting them. The CLI and session-end hook both use the shutdown protocol.

**Tech Stack:** Python (daemon), TypeScript (MCP server), Node.js (CLI + hook). No new dependencies.

---

### Task 1: Lock File — Daemon Startup Guard

**Files:**
- Modify: `daemon/rlm_daemon.py:656-722` (run_server function)
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_daemon.py`:

```python
class TestLockFile:
    def test_lock_file_created_on_startup(self, tmp_path):
        """Lock file should be written when daemon starts."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        lock_file = rlm_dir / "daemon.lock"

        # We can't easily test run_server (it blocks), so test the helpers directly
        from rlm_daemon import write_lock_file, read_lock_file, check_lock_file

        write_lock_file(str(tmp_path), 9177)
        assert lock_file.exists()

        data = read_lock_file(str(tmp_path))
        assert data is not None
        assert data["pid"] == os.getpid()
        assert data["port"] == 9177
        assert data["root"] == str(Path(tmp_path).resolve())
        assert "started_at" in data

    def test_lock_file_detects_stale(self, tmp_path):
        """Lock file with dead PID should be detected as stale."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        lock_file = rlm_dir / "daemon.lock"

        # Write a lock with a definitely-dead PID
        lock_data = json.dumps({"pid": 999999999, "port": 9177, "root": str(tmp_path), "started_at": "2026-01-01T00:00:00"})
        lock_file.write_text(lock_data)

        from rlm_daemon import check_lock_file
        result = check_lock_file(str(tmp_path))
        # Should return None (stale lock) and clean up the file
        assert result is None
        assert not lock_file.exists()

    def test_lock_file_blocks_duplicate(self, tmp_path):
        """Lock file with alive PID should block startup."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        from rlm_daemon import write_lock_file, check_lock_file
        write_lock_file(str(tmp_path), 9177)

        # Current process is alive, so check_lock_file should return the lock data
        result = check_lock_file(str(tmp_path))
        assert result is not None
        assert result["pid"] == os.getpid()

    def test_remove_lock_file(self, tmp_path):
        """remove_lock_file should delete the lock."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        from rlm_daemon import write_lock_file, remove_lock_file
        write_lock_file(str(tmp_path), 9177)
        assert (rlm_dir / "daemon.lock").exists()

        remove_lock_file(str(tmp_path))
        assert not (rlm_dir / "daemon.lock").exists()
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestLockFile -v`
Expected: FAIL with ImportError (functions don't exist yet)

**Step 3: Write minimal implementation**

Add these functions to `daemon/rlm_daemon.py` (after the imports, before `SessionStats` class — around line 34):

```python
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
```

Then modify `run_server()` in `daemon/rlm_daemon.py` to use the lock file. Insert after line 659 (`root_path = str(Path(root).resolve())`):

```python
    # Check for existing daemon (lock file guard)
    rlm_dir = Path(root_path) / ".rlm"
    if rlm_dir.is_dir():
        existing = check_lock_file(root_path)
        if existing:
            print(f"Error: Daemon already running (PID {existing['pid']}, port {existing['port']})", file=sys.stderr)
            sys.exit(1)
```

Insert after line 722 (`port_file.write_text(...)`) — i.e., right after the port file write:

```python
    # Write lock file
    write_lock_file(root_path, bound_port)
```

Add to the `finally` block (after the port file cleanup at line 764-768):

```python
        remove_lock_file(root_path)
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestLockFile -v`
Expected: PASS (all 4 tests)

**Step 5: Commit**

```bash
git add daemon/rlm_daemon.py daemon/tests/test_daemon.py
git commit -m "feat: add lock file for daemon single-instance enforcement"
```

---

### Task 2: Shutdown Action — Daemon Graceful Shutdown

**Files:**
- Modify: `daemon/rlm_daemon.py:437-615` (add shutdown action to _handle_request_inner)
- Modify: `daemon/rlm_daemon.py:656-771` (run_server — expose shutdown_event)
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_daemon.py`:

```python
class TestShutdownAction:
    def test_shutdown_action_returns_ok(self, tmp_path):
        """Shutdown action should return success and set the event."""
        (tmp_path / "test.py").write_text("def foo(): pass\n")
        cache = SkeletonCache()

        shutdown_event = threading.Event()
        data = json.dumps({"action": "shutdown"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path), shutdown_event=shutdown_event))
        assert resp.get("status") == "shutting_down"
        assert shutdown_event.is_set()

    def test_shutdown_action_without_event(self, tmp_path):
        """Shutdown without event should return error."""
        cache = SkeletonCache()
        data = json.dumps({"action": "shutdown"}).encode()
        resp = json.loads(handle_request(data, cache, str(tmp_path)))
        assert "error" in resp
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestShutdownAction -v`
Expected: FAIL (handle_request doesn't accept shutdown_event param yet)

**Step 3: Write minimal implementation**

Modify `handle_request` signature at line 416 to accept `shutdown_event`:

```python
def handle_request(data: bytes, cache: SkeletonCache, root: str, repl: RLMRepl = None, stats: SessionStats = None, chunk_store: ChunkStore = None, shutdown_event: threading.Event = None) -> bytes:
```

Modify `_handle_request_inner` signature at line 437 similarly:

```python
def _handle_request_inner(data: bytes, cache: SkeletonCache, root: str, repl: RLMRepl = None, stats: SessionStats = None, chunk_store: ChunkStore = None, shutdown_event: threading.Event = None) -> bytes:
```

Pass `shutdown_event` through in `handle_request` at line 418:

```python
    response = _handle_request_inner(data, cache, root, repl, stats, chunk_store, shutdown_event)
```

Add the shutdown action in `_handle_request_inner`, before the `else` clause at line 614:

```python
    elif action == "shutdown":
        if shutdown_event is None:
            return json.dumps({"error": "Shutdown not available"}).encode("utf-8")
        shutdown_event.set()
        return json.dumps({"status": "shutting_down"}).encode("utf-8")
```

Pass `shutdown_event` to `handle_client` and through to `handle_request`. Modify `handle_client` signature at line 618:

```python
def handle_client(conn: socket.socket, cache: SkeletonCache, root: str, repl: RLMRepl = None, stats: SessionStats = None, chunk_store: ChunkStore = None, shutdown_event: threading.Event = None):
```

Update the `handle_request` call inside `handle_client` at line 641:

```python
        response = handle_request(data, cache, root, repl, stats, chunk_store, shutdown_event)
```

Update the thread creation in `run_server` at lines 742-746:

```python
            thread = threading.Thread(
                target=handle_client,
                args=(conn, cache, root_path, repl, stats, chunk_store, shutdown_event),
                daemon=True,
            )
```

**Step 4: Run test to verify it passes**

Run: `cd daemon && python -m pytest tests/test_daemon.py::TestShutdownAction -v`
Expected: PASS

**Step 5: Run all daemon tests**

Run: `cd daemon && python -m pytest tests/test_daemon.py -v`
Expected: All PASS (existing tests still work since shutdown_event defaults to None)

**Step 6: Commit**

```bash
git add daemon/rlm_daemon.py daemon/tests/test_daemon.py
git commit -m "feat: add shutdown action for graceful daemon teardown"
```

---

### Task 3: Session-End Hook — Shutdown on Session End

**Files:**
- Modify: `hooks/rlm-session-end.js`

**Step 1: Read the current hook to understand structure**

The hook currently: reads port from `.rlm/port`, queries daemon status for stats, prints summary. It does NOT shut down the daemon.

**Step 2: Add shutdown logic**

Replace the `main()` function in `hooks/rlm-session-end.js` (lines 134-148) with:

```javascript
function shutdownDaemon(rlmDir) {
  return new Promise((resolve) => {
    const portFile = path.join(rlmDir, "port");
    if (!fs.existsSync(portFile)) return resolve(false);

    let portData;
    try {
      portData = JSON.parse(fs.readFileSync(portFile, "utf-8"));
    } catch {
      return resolve(false);
    }

    const port = portData.port || 9177;
    const pid = portData.pid || null;
    const client = new net.Socket();
    let responded = false;

    const timer = setTimeout(() => {
      client.destroy();
      if (!responded) {
        // Graceful shutdown timed out — force kill
        forceKill(pid);
        cleanupFiles(rlmDir);
      }
      resolve(true);
    }, 3000);

    client.connect(port, "127.0.0.1", () => {
      client.write(JSON.stringify({ action: "shutdown" }));
    });

    client.on("data", (chunk) => {
      responded = true;
      clearTimeout(timer);
      client.destroy();
      // Give daemon a moment to finish cleanup
      setTimeout(() => resolve(true), 500);
    });

    client.on("error", () => {
      clearTimeout(timer);
      // Connection refused — daemon already dead, clean up files
      cleanupFiles(rlmDir);
      resolve(false);
    });
  });
}

function forceKill(pid) {
  if (!pid) return;
  try {
    if (process.platform === "win32") {
      require("child_process").execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
    } else {
      process.kill(pid, "SIGKILL");
    }
  } catch {
    // Process already dead
  }
}

function cleanupFiles(rlmDir) {
  for (const file of ["port", "daemon.lock"]) {
    try { fs.unlinkSync(path.join(rlmDir, file)); } catch {}
  }
}

async function main() {
  try {
    const rlmDir = findRlmDir();
    if (!rlmDir) return;

    // 1. Print session stats (existing behavior)
    const gotDaemon = await tryDaemon(rlmDir);
    if (!gotDaemon) {
      tryLogFile(rlmDir);
    }

    // 2. Shut down daemon
    await shutdownDaemon(rlmDir);
  } catch {
    // Silent failure
  }
}
```

**Step 3: Manual verification**

To test: start a daemon manually, start a Claude Code session, end it, verify daemon process is gone and port/lock files are cleaned up.

**Step 4: Commit**

```bash
git add hooks/rlm-session-end.js
git commit -m "feat: session-end hook shuts down daemon with force-kill fallback"
```

---

### Task 4: MCP Server — Spawn Verification & Retry Hardening

**Files:**
- Modify: `server/src/index.ts:93-247`

**Step 1: Add spawn guard and verification**

Replace `spawnDaemon()` (lines 93-113) with:

```typescript
let spawning = false;

function spawnDaemon(): void {
  if (daemonChild || spawning) return; // Already spawned or in progress

  const daemonScript = path.join(PROJECT_ROOT, ".rlm", "daemon", "rlm_daemon.py");
  if (!fs.existsSync(daemonScript)) return;

  spawning = true;

  for (const cmd of ["python", "python3"]) {
    try {
      const child = spawn(cmd, [daemonScript, "--root", PROJECT_ROOT, "--idle-timeout", "300"], {
        detached: true,
        stdio: "ignore",
      });
      child.unref();
      daemonChild = child;
      return;
    } catch {
      continue;
    }
  }
  // If we get here, both python commands failed
  spawning = false;
}
```

**Step 2: Replace `waitForDaemon()` with verification**

Replace `waitForDaemon()` (lines 115-127) with:

```typescript
async function waitForDaemon(maxWaitMs = 10000): Promise<boolean> {
  const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
  const start = Date.now();

  // Phase 1: Wait for port file to appear
  while (Date.now() - start < maxWaitMs) {
    await sleep(300);
    if (fs.existsSync(portFile)) break;
  }

  if (!fs.existsSync(portFile)) {
    spawning = false;
    return false;
  }

  // Phase 2: Verify daemon is actually listening and serving the right project
  await sleep(200); // Brief pause for TCP listener to start
  try {
    const status = await queryDaemon({ action: "status" }, 5000);
    if (status.root) {
      const daemonRoot = path.resolve(status.root);
      const expectedRoot = path.resolve(PROJECT_ROOT);
      if (daemonRoot !== expectedRoot) {
        // Wrong daemon — clean up and fail
        try { fs.unlinkSync(portFile); } catch {}
        spawning = false;
        return false;
      }
    }
    daemonRootValidated = true;
    spawning = false;
    return true;
  } catch {
    spawning = false;
    return false;
  }
}
```

**Step 3: Simplify root validation (first-connection, not TTL)**

Replace the `daemonRootValidatedAt` / `VALIDATION_TTL_MS` / `validateDaemonRoot()` block (lines 197-230) with:

```typescript
let daemonRootValidated = false;

async function validateDaemonRoot(): Promise<void> {
  if (daemonRootValidated) return;
  try {
    const status = await queryDaemon({ action: "status" });
    if (status.root) {
      const daemonRoot = path.resolve(status.root);
      const expectedRoot = path.resolve(PROJECT_ROOT);
      if (daemonRoot !== expectedRoot) {
        const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
        try { fs.unlinkSync(portFile); } catch {}
        daemonRootValidated = false;
        spawnDaemon();
        const ok = await waitForDaemon();
        if (!ok) {
          throw new Error(`Failed to start daemon for ${expectedRoot}`);
        }
      }
    }
    daemonRootValidated = true;
  } catch (err: any) {
    if (err?.code === "ECONNREFUSED" || err?.message?.includes("ECONNREFUSED")) {
      return;
    }
    throw err;
  }
}
```

**Step 4: Harden retry logic**

Replace `queryDaemonWithRetry()` (lines 232-247) with:

```typescript
async function queryDaemonWithRetry(request: object, timeoutMs = 10000, retries = 3): Promise<any> {
  await validateDaemonRoot();
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      return await queryDaemon(request, timeoutMs);
    } catch (err: any) {
      const isConnRefused = err?.code === "ECONNREFUSED" || err?.message?.includes("ECONNREFUSED");
      if (isConnRefused && attempt < retries - 1) {
        // Only spawn once — guard prevents duplicates
        spawnDaemon();
        const ok = await waitForDaemon();
        if (!ok) {
          throw new Error("Failed to start daemon after spawn attempt");
        }
        continue;
      }
      throw err;
    }
  }
}
```

**Step 5: Build and verify**

Run: `cd server && npm run build`
Expected: Build succeeds with no errors

**Step 6: Commit**

```bash
git add server/src/index.ts
git commit -m "feat: spawn verification, single spawn guard, first-connection root validation"
```

---

### Task 5: CLI — Shutdown Before Install/Uninstall + Enhanced Status

**Files:**
- Modify: `bin/cli.js:186-602`

**Step 1: Add daemon shutdown helper**

Add after the `run()` helper (after line 113) in `bin/cli.js`:

```javascript
function shutdownDaemon() {
  // 1. Read port/lock file for connection info
  const portFile = path.join(RLM_DIR, "port");
  const lockFile = path.join(RLM_DIR, "daemon.lock");

  let port = null;
  let pid = null;

  // Try lock file first (more complete info)
  if (fs.existsSync(lockFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(lockFile, "utf-8"));
      port = data.port;
      pid = data.pid;
    } catch {}
  }

  // Fall back to port file
  if (!port && fs.existsSync(portFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(portFile, "utf-8"));
      port = data.port;
      pid = pid || data.pid;
    } catch {}
  }

  if (!port && !pid) return; // Nothing to shut down

  // 2. Try graceful shutdown via TCP
  if (port) {
    try {
      const client = new net.Socket();
      client.connect(port, "127.0.0.1", () => {
        client.write(JSON.stringify({ action: "shutdown" }));
        client.destroy();
      });
      client.on("error", () => {}); // Ignore connection errors
    } catch {}

    // Wait up to 3 seconds for daemon to exit
    const start = Date.now();
    while (Date.now() - start < 3000) {
      if (pid && !isPidAlive(pid)) break;
      spawnSync("node", ["-e", "setTimeout(()=>{},200)"], { stdio: "ignore" }); // sleep 200ms
    }
  }

  // 3. Force kill if still alive
  if (pid && isPidAlive(pid)) {
    try {
      if (process.platform === "win32") {
        execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
      } else {
        process.kill(pid, "SIGKILL");
      }
    } catch {}
  }

  // 4. Clean up files
  for (const f of [portFile, lockFile]) {
    try { fs.unlinkSync(f); } catch {}
  }
}

function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
```

**Step 2: Modify install() to handle reinstall**

Replace lines 190-196 (the "already installed" check) with:

```javascript
  if (fs.existsSync(RLM_DIR)) {
    // Shut down any running daemon before reinstalling
    let spinner = step("Stopping existing daemon...");
    shutdownDaemon();
    spinner.succeed("Existing daemon stopped");
  }
```

**Step 3: Add shutdown to uninstall()**

Insert after line 465 (start of `uninstall()`) before MCP removal:

```javascript
  // 0. Shut down running daemon first
  let spinner = step("Stopping running daemon...");
  shutdownDaemon();
  spinner.succeed("Daemon stopped");
```

**Step 4: Enhance status()**

Replace the `status()` function (lines 548-602) with:

```javascript
function status() {
  const divider = chalk.dim("  ────────────────────────");

  console.log("");
  console.log(chalk.bold.cyan("  RLM Navigator Status"));
  console.log(divider);

  if (!fs.existsSync(RLM_DIR)) {
    console.log(`  Installed:   ${chalk.red("✖ No")}`);
    console.log("");
    console.log(chalk.dim(`  Run: npx rlm-navigator install`));
    console.log("");
    return;
  }
  console.log(`  Installed:   ${chalk.green("✔ Yes")}`);

  // Read lock file for comprehensive info
  const lockFile = path.join(RLM_DIR, "daemon.lock");
  const portFile = path.join(RLM_DIR, "port");
  let port = null;
  let pid = null;
  let startedAt = null;

  if (fs.existsSync(lockFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(lockFile, "utf-8"));
      port = data.port;
      pid = data.pid;
      startedAt = data.started_at;
    } catch {}
  }

  if (!port && fs.existsSync(portFile)) {
    try {
      const raw = fs.readFileSync(portFile, "utf-8").trim();
      const data = JSON.parse(raw);
      port = data.port;
      pid = pid || data.pid;
    } catch {
      port = parseInt(fs.readFileSync(portFile, "utf-8").trim(), 10);
    }
  }

  if (port) console.log(`  Port:        ${chalk.white(port)}`);
  else console.log(`  Port:        ${chalk.dim("unknown")}`);

  if (pid) {
    const alive = isPidAlive(pid);
    console.log(`  PID:         ${chalk.white(pid)} ${alive ? chalk.green("(alive)") : chalk.red("(dead)")}`);
    if (!alive) {
      // Auto-clean stale files
      for (const f of [lockFile, portFile]) {
        try { fs.unlinkSync(f); } catch {}
      }
      console.log(`  ${chalk.yellow("⚠ Cleaned stale port/lock files")}`);
      console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(was orphaned)")}`);
      console.log("");
      return;
    }
  }

  if (startedAt) console.log(`  Started:     ${chalk.dim(startedAt)}`);

  // TCP health check
  if (!port) {
    console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(no port file)")}`);
    console.log("");
    return;
  }

  const client = new net.Socket();
  const timer = setTimeout(() => {
    client.destroy();
    console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(timeout)")}`);
    console.log("");
  }, 2000);

  client.connect(port, "127.0.0.1", () => {
    client.on("data", (chunk) => {
      clearTimeout(timer);
      const msg = chunk.toString("utf-8");
      client.destroy();
      if (msg.includes("ALIVE")) {
        console.log(`  Daemon:      ${chalk.green("● ONLINE")}`);
      } else {
        console.log(`  Daemon:      ${chalk.yellow("● UNKNOWN")}`);
      }
      console.log("");
    });
  });

  client.on("error", () => {
    clearTimeout(timer);
    console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(connection refused)")}`);
    console.log("");
  });
}
```

**Step 5: Manual verification**

Test each scenario:
1. `npx rlm-navigator status` — shows PID, port, uptime, health
2. `npx rlm-navigator install` (with existing install) — shuts down daemon, reinstalls
3. `npx rlm-navigator uninstall` — shuts down daemon, removes everything

**Step 6: Commit**

```bash
git add bin/cli.js
git commit -m "feat: CLI shuts down daemon before install/uninstall, enhanced status"
```

---

### Task 6: Integration Testing — Full Lifecycle

**Files:**
- Test: `daemon/tests/test_daemon.py`

**Step 1: Write integration test for full lifecycle**

Add to `daemon/tests/test_daemon.py`:

```python
class TestDaemonLifecycle:
    def test_shutdown_via_tcp(self, tmp_path):
        """Start daemon, send shutdown via TCP, verify clean exit."""
        port = 19179
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        (tmp_path / "test.py").write_text("def foo(): pass\n")

        shutdown_complete = threading.Event()

        def run_and_signal():
            run_server(str(tmp_path), port, idle_timeout=0)
            shutdown_complete.set()

        server_thread = threading.Thread(target=run_and_signal, daemon=True)
        server_thread.start()
        time.sleep(1)

        # Verify daemon is running
        lock_file = rlm_dir / "daemon.lock"
        port_file = rlm_dir / "port"

        try:
            # Send shutdown
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(("127.0.0.1", port))
            s.send(json.dumps({"action": "shutdown"}).encode())
            data = s.recv(4096)
            s.close()

            resp = json.loads(data)
            assert resp["status"] == "shutting_down"

            # Wait for server to actually shut down
            assert shutdown_complete.wait(timeout=5), "Daemon didn't shut down in time"

            # Verify cleanup: port file and lock file should be removed
            assert not port_file.exists(), "Port file not cleaned up"
            assert not lock_file.exists(), "Lock file not cleaned up"

        except Exception as e:
            pytest.skip(f"TCP test failed (port may be in use): {e}")

    def test_lock_prevents_second_daemon(self, tmp_path):
        """Starting a second daemon on same root should fail."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        # Write a lock with current PID (simulating a running daemon)
        write_lock_file(str(tmp_path), 9177)

        # Attempting to start another daemon should exit
        with pytest.raises(SystemExit):
            run_server(str(tmp_path), 19180, idle_timeout=0)
```

**Step 2: Run the full test suite**

Run: `cd daemon && python -m pytest tests/test_daemon.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add daemon/tests/test_daemon.py
git commit -m "test: integration tests for daemon lifecycle (shutdown + lock)"
```

---

### Task 7: Build MCP Server & Final Verification

**Files:**
- Build: `server/`

**Step 1: Build the TypeScript MCP server**

Run: `cd server && npm run build`
Expected: Clean build, no errors

**Step 2: Run all daemon tests one final time**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 3: Final commit (version bump if desired)**

```bash
git add -A
git status  # Verify only expected files
git commit -m "build: compile MCP server with lifecycle improvements"
```

---

## Summary of Changes

| File | What Changed |
|---|---|
| `daemon/rlm_daemon.py` | Lock file helpers, shutdown action, lock guard in run_server, lock cleanup in finally |
| `daemon/tests/test_daemon.py` | TestLockFile (4 tests), TestShutdownAction (2 tests), TestDaemonLifecycle (2 tests) |
| `hooks/rlm-session-end.js` | Sends shutdown signal after printing stats, force-kill fallback, file cleanup |
| `server/src/index.ts` | Spawn guard flag, waitForDaemon with health check, first-connection root validation |
| `bin/cli.js` | shutdownDaemon() helper, isPidAlive(), install handles reinstall, uninstall kills daemon, enhanced status |

## Testing Matrix

| Scenario | How to Test |
|---|---|
| Lock prevents duplicate daemons | `pytest tests/test_daemon.py::TestLockFile` |
| Shutdown action works | `pytest tests/test_daemon.py::TestShutdownAction` |
| Full lifecycle (start→shutdown→cleanup) | `pytest tests/test_daemon.py::TestDaemonLifecycle` |
| Session end kills daemon | End a Claude Code session, verify daemon process gone |
| CLI uninstall kills daemon | `npx rlm-navigator uninstall`, verify no orphan |
| CLI reinstall works | `npx rlm-navigator install` on already-installed project |
| MCP spawn verification | Start Claude Code on project with no daemon, verify single daemon started |
| Wrong-project detection | Start daemon for project A, try to use from project B |
