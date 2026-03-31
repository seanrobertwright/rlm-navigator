# Daemon Reliability Implementation Plan

> **For agentic workers:** REQUIRED: Use lril-superpowers:subagent-driven-development (if subagents available) or lril-superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate persistent "Failed to start daemon after spawn attempt" errors by adding a session-start hook with stale cleanup + proactive spawn, hardening MCP server retry logic, and updating install/uninstall/status flows.

**Architecture:** A new `SessionStart` hook proactively cleans stale state and pre-warms the daemon before any tool call. The MCP server's retry path gets longer timeouts, file-based stderr capture, and diagnostic error messages as a safety net. The CLI's hook registration is parameterized to handle both hook types.

**Tech Stack:** Node.js (hooks, CLI), TypeScript (MCP server), TCP sockets

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `hooks/rlm-session-start.js` | Create | Session-start hook: stale cleanup, pre-flight, spawn, verify |
| `server/src/index.ts` | Modify | Timeout increase, stderr-to-file, diagnostic error messages |
| `bin/cli.js` | Modify | Parameterize hook helpers, register SessionStart, status display |
| `package.json` | Modify | Version bump 2.0.0 -> 2.1.0 |

---

## Chunk 1: Session-Start Hook

### Task 1: Create session-start hook with stale cleanup and proactive spawn

**Files:**
- Create: `hooks/rlm-session-start.js`
- Reference: `hooks/rlm-session-end.js` (shared patterns)
- Reference: `bin/cli.js:534-541` (`isPidAlive` pattern)
- Reference: `hooks/rlm-session-end.js:136-147` (`forceKill` pattern)

- [ ] **Step 1: Create `hooks/rlm-session-start.js` with full implementation**

```javascript
#!/usr/bin/env node
"use strict";

/**
 * Claude Code SessionStart hook — cleans stale daemon state,
 * verifies prerequisites, and proactively starts the daemon.
 *
 * Always exits 0 — never blocks a session start.
 * All failures are logged with actionable diagnostics.
 */

const fs = require("fs");
const path = require("path");
const net = require("net");
const { execSync, spawn } = require("child_process");

// ── Helpers (self-contained, mirroring patterns from session-end hook) ──

function findRlmDir() {
  let dir = process.cwd();
  while (true) {
    const candidate = path.join(dir, ".rlm");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
      return { rlmDir: candidate, projectRoot: dir };
    }
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
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

function forceKill(pid) {
  if (!pid) return;
  try {
    if (process.platform === "win32") {
      execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
    } else {
      process.kill(pid, "SIGKILL");
    }
  } catch {
    // Already dead
  }
}

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch {
    return null;
  }
}

function cleanFiles(rlmDir) {
  for (const file of ["port", "daemon.lock"]) {
    try { fs.unlinkSync(path.join(rlmDir, file)); } catch {}
  }
}

function healthCheck(port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const client = new net.Socket();
    const timer = setTimeout(() => {
      client.destroy();
      resolve(false);
    }, timeoutMs);

    client.connect(port, "127.0.0.1", () => {
      client.on("data", (chunk) => {
        clearTimeout(timer);
        client.destroy();
        resolve(chunk.toString("utf-8").includes("ALIVE"));
      });
    });

    client.on("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

function findPython() {
  for (const cmd of ["python", "python3"]) {
    try {
      execSync(`${cmd} --version`, { stdio: "pipe" });
      return cmd;
    } catch {
      continue;
    }
  }
  return null;
}

function waitForPortFile(rlmDir, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const portFile = path.join(rlmDir, "port");
    const start = Date.now();
    const interval = setInterval(() => {
      if (fs.existsSync(portFile)) {
        clearInterval(interval);
        resolve(true);
      } else if (Date.now() - start >= timeoutMs) {
        clearInterval(interval);
        resolve(false);
      }
    }, 300);
  });
}

// ── Main ──

async function main() {
  const found = findRlmDir();
  if (!found) return; // Not an RLM project

  const { rlmDir, projectRoot } = found;

  // Step 1: Stale file cleanup
  const portFile = path.join(rlmDir, "port");
  const lockFile = path.join(rlmDir, "daemon.lock");
  const portData = readJsonFile(portFile);
  const lockData = readJsonFile(lockFile);
  const pid = lockData?.pid || portData?.pid || null;
  const port = portData?.port || lockData?.port || null;

  if (pid) {
    if (isPidAlive(pid)) {
      // PID alive — check if actually responsive
      if (port) {
        const alive = await healthCheck(port);
        if (alive) {
          console.log(`[RLM] Daemon already running on port ${port}`);
          return;
        }
        // Alive PID but unresponsive — force kill
        console.log(`[RLM] Daemon PID ${pid} alive but unresponsive, killing...`);
        forceKill(pid);
        cleanFiles(rlmDir);
      }
    } else {
      // PID dead — clean stale files
      console.log(`[RLM] Cleaned stale daemon state (PID ${pid} no longer running)`);
      cleanFiles(rlmDir);
    }
  }

  // Step 2: Pre-flight checks
  const daemonScript = path.join(rlmDir, "daemon", "rlm_daemon.py");
  if (!fs.existsSync(daemonScript)) {
    console.log(`[RLM] Cannot start daemon: ${daemonScript} not found. Run 'npx rlm-navigator status' to diagnose.`);
    return;
  }

  const pythonCmd = findPython();
  if (!pythonCmd) {
    console.log("[RLM] Cannot start daemon: Python not found. Run 'npx rlm-navigator status' to diagnose.");
    return;
  }

  // Step 3: Spawn daemon (stderr to file for diagnostics)
  const stderrLogPath = path.join(rlmDir, "daemon-start.log");
  const stderrFd = fs.openSync(stderrLogPath, "w");
  try {
    const child = spawn(pythonCmd, [daemonScript, "--root", projectRoot, "--idle-timeout", "0"], {
      detached: true,
      stdio: ["ignore", "ignore", stderrFd],
    });
    child.unref();
  } catch (err) {
    fs.closeSync(stderrFd);
    console.log(`[RLM] Failed to spawn daemon: ${err.message}. Run 'npx rlm-navigator status' to diagnose.`);
    return;
  }
  fs.closeSync(stderrFd);

  // Step 4: Wait and verify
  const appeared = await waitForPortFile(rlmDir, 15000);
  if (!appeared) {
    let diag = "";
    try {
      const log = fs.readFileSync(stderrLogPath, "utf-8").trim();
      if (log) diag = ` Stderr: ${log.slice(-1024)}`;
    } catch {}
    console.log(`[RLM] Failed to start daemon: port file not created after 15s.${diag} Run 'npx rlm-navigator status' to diagnose.`);
    return;
  }

  const newPortData = readJsonFile(path.join(rlmDir, "port"));
  if (newPortData?.port) {
    const alive = await healthCheck(newPortData.port);
    if (alive) {
      console.log(`[RLM] Daemon started on port ${newPortData.port} (PID ${newPortData.pid || "unknown"})`);
    } else {
      let diag = "";
      try {
        const log = fs.readFileSync(stderrLogPath, "utf-8").trim();
        if (log) diag = ` Stderr: ${log.slice(-1024)}`;
      } catch {}
      console.log(`[RLM] Daemon port file found but health check failed.${diag} Run 'npx rlm-navigator status' to diagnose.`);
    }
  }
}

main().catch((err) => {
  console.log(`[RLM] Session-start hook error: ${err.message}`);
});
```

- [ ] **Step 2: Manually test the hook in a project with RLM installed**

Run from a project directory that has `.rlm/` installed:
```bash
node hooks/rlm-session-start.js
```
Expected: Either `[RLM] Daemon started on port XXXX` or `[RLM] Daemon already running on port XXXX`

- [ ] **Step 3: Test stale state cleanup by creating a fake stale port file**

```bash
# Create fake stale state (use a PID that doesn't exist)
echo '{"port": 9177, "pid": 99999}' > .rlm/port
echo '{"pid": 99999, "port": 9177, "root": ".", "started_at": "2026-01-01"}' > .rlm/daemon.lock
node hooks/rlm-session-start.js
```
Expected: `[RLM] Cleaned stale daemon state (PID 99999 no longer running)` followed by daemon spawn

- [ ] **Step 4: Commit**

```bash
git add hooks/rlm-session-start.js
git commit -m "feat: add session-start hook with stale cleanup and proactive daemon spawn"
```

---

## Chunk 2: MCP Server Retry Hardening

### Task 2: Increase `waitForDaemon()` timeout and add stderr capture to `spawnDaemon()`

**Files:**
- Modify: `server/src/index.ts:66-127` (spawnDaemon + waitForDaemon)

- [ ] **Step 1: Add module-level `daemonStderrPath` variable**

In `server/src/index.ts`, after line 58 (`let daemonChild: ChildProcess | null = null;`), add:

```typescript
let daemonStderrPath: string | null = null;
```

- [ ] **Step 2: Modify `spawnDaemon()` to redirect stderr to file**

Replace the `spawnDaemon()` function (lines 66-89) with:

```typescript
function spawnDaemon(): void {
  if (daemonChild || spawning) return;

  const daemonScript = path.join(PROJECT_ROOT, ".rlm", "daemon", "rlm_daemon.py");
  if (!fs.existsSync(daemonScript)) return;

  spawning = true;
  daemonStderrPath = path.join(PROJECT_ROOT, ".rlm", "daemon-start.log");

  for (const cmd of ["python", "python3"]) {
    try {
      const stderrFd = fs.openSync(daemonStderrPath, "w");
      try {
        const child = spawn(cmd, [daemonScript, "--root", PROJECT_ROOT, "--idle-timeout", "0"], {
          detached: true,
          stdio: ["ignore", "ignore", stderrFd],
        });
        child.unref();
        daemonChild = child;
        return;
      } catch (err) {
        // Write spawn error to log file for diagnostics
        try {
          fs.writeFileSync(daemonStderrPath, `Failed to spawn with '${cmd}': ${err}\n`, { flag: "a" });
        } catch {}
        continue;
      } finally {
        fs.closeSync(stderrFd);
      }
    }
  }
  spawning = false;
}
```

- [ ] **Step 3: Increase `waitForDaemon()` timeout from 10s to 20s and add phase logging**

Change line 91 default and add `console.error` logging at each phase:

```typescript
// Before:
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
  await sleep(200);

// After:
async function waitForDaemon(maxWaitMs = 20000): Promise<boolean> {
  const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
  const start = Date.now();

  // Phase 1: Wait for port file to appear
  console.error("[RLM] Waiting for port file...");
  while (Date.now() - start < maxWaitMs) {
    await sleep(300);
    if (fs.existsSync(portFile)) break;
  }

  if (!fs.existsSync(portFile)) {
    console.error("[RLM] Port file not found after timeout");
    spawning = false;
    return false;
  }

  // Phase 2: Verify daemon is actually listening and serving the right project
  console.error("[RLM] Port file found, verifying root...");
  await sleep(200);
```

- [ ] **Step 4: Build and verify compilation**

```bash
cd server && npm run build
```
Expected: No TypeScript errors

- [ ] **Step 5: Commit**

```bash
git add server/src/index.ts
git commit -m "feat: harden daemon spawn with stderr capture and 20s timeout"
```

### Task 3: Add diagnostic error messages to `queryDaemonWithRetry()`

**Files:**
- Modify: `server/src/index.ts:193-215` (queryDaemonWithRetry)

- [ ] **Step 1: Add `execSync` import**

At line 17, add `execSync` to the child_process import:
```typescript
// Before:
import { spawn, ChildProcess } from "node:child_process";
// After:
import { spawn, execSync, ChildProcess } from "node:child_process";
```

- [ ] **Step 2: Add a `gatherDiagnostics()` helper function**

Add before `queryDaemonWithRetry()` (before line 193):

```typescript
function gatherDiagnostics(): string {
  const parts: string[] = [];

  // Check daemon script
  const daemonScript = path.join(PROJECT_ROOT, ".rlm", "daemon", "rlm_daemon.py");
  parts.push(`daemon_script=${fs.existsSync(daemonScript) ? "exists" : "missing"}`);

  // Check Python availability
  let pythonFound = "not_found";
  for (const cmd of ["python", "python3"]) {
    try {
      execSync(`${cmd} --version`, { stdio: "pipe" });
      pythonFound = `found(${cmd})`;
      break;
    } catch {}
  }
  parts.push(`python=${pythonFound}`);

  // Check port file
  const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
  if (fs.existsSync(portFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(portFile, "utf-8"));
      const alive = data.pid ? isPidAlive(data.pid) : "no_pid";
      parts.push(`port_file=exists(port=${data.port},pid=${data.pid},alive=${alive})`);
    } catch {
      parts.push("port_file=exists(parse_error)");
    }
  } else {
    parts.push("port_file=missing");
  }

  // Check lock file
  const lockFile = path.join(PROJECT_ROOT, ".rlm", "daemon.lock");
  if (fs.existsSync(lockFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(lockFile, "utf-8"));
      const alive = data.pid ? isPidAlive(data.pid) : "no_pid";
      parts.push(`lock_file=exists(pid=${data.pid},alive=${alive})`);
    } catch {
      parts.push("lock_file=exists(parse_error)");
    }
  } else {
    parts.push("lock_file=missing");
  }

  // Check daemon stderr log
  if (daemonStderrPath) {
    try {
      const log = fs.readFileSync(daemonStderrPath, "utf-8").trim();
      if (log) {
        const lastKb = log.slice(-1024);
        parts.push(`spawn_stderr=${JSON.stringify(lastKb)}`);
      }
    } catch {}
  }

  return parts.join(", ");
}
```

- [ ] **Step 3: Update `queryDaemonWithRetry()` to use diagnostics on failure**

Replace the error throw at line 208:
```typescript
// Before:
throw new Error("Failed to start daemon after spawn attempt");

// After:
const diag = gatherDiagnostics();
throw new Error(
  `Failed to start daemon after spawn attempt.\n` +
  `Diagnostics: ${diag}\n` +
  `Run 'npx rlm-navigator status' to diagnose further.`
);
```

- [ ] **Step 4: Build and verify compilation**

```bash
cd server && npm run build
```
Expected: No TypeScript errors

- [ ] **Step 5: Commit**

```bash
git add server/src/index.ts
git commit -m "feat: add diagnostic error messages to daemon retry logic"
```

---

## Chunk 3: CLI Install Integration

### Task 4: Parameterize `installHook()` and `uninstallHook()` in cli.js

**Files:**
- Modify: `bin/cli.js:457-608` (registerHook, installHook, uninstallHook)

- [ ] **Step 1: Refactor `installHook()` to accept parameters**

Replace `installHook()` (lines 547-582) with:

```javascript
function installHook(hookType, hookFilename) {
  // 1. Copy hook file
  const srcHook = path.join(PKG_ROOT, "hooks", hookFilename);
  const destHookDir = path.join(RLM_DIR, "hooks");
  const destHook = path.join(destHookDir, hookFilename);
  fs.mkdirSync(destHookDir, { recursive: true });
  fs.copyFileSync(srcHook, destHook);

  // 2. Merge into .claude/settings.json
  const settingsPath = path.join(CWD, ".claude", "settings.json");
  let settings = {};
  if (fs.existsSync(settingsPath)) {
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
    } catch {
      settings = {};
    }
  }

  if (!settings.hooks) settings.hooks = {};
  if (!Array.isArray(settings.hooks[hookType])) settings.hooks[hookType] = [];

  const hookCmd = `node "${destHook.replace(/\\/g, "/")}"`;
  const alreadyRegistered = settings.hooks[hookType].some((entry) =>
    (entry.hooks || []).some((h) => h.command && h.command.includes(hookFilename))
  );

  if (!alreadyRegistered) {
    settings.hooks[hookType].push({
      hooks: [{ type: "command", command: hookCmd }],
    });
  }

  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
}
```

- [ ] **Step 2: Refactor `uninstallHook()` to accept parameters**

Replace `uninstallHook()` (lines 584-608) with:

```javascript
function uninstallHook(hookType, hookFilename) {
  const settingsPath = path.join(CWD, ".claude", "settings.json");
  if (!fs.existsSync(settingsPath)) return;

  let settings;
  try {
    settings = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
  } catch {
    return;
  }

  if (settings.hooks && Array.isArray(settings.hooks[hookType])) {
    settings.hooks[hookType] = settings.hooks[hookType].filter((entry) =>
      !(entry.hooks || []).some((h) => h.command && h.command.includes(hookFilename))
    );
    if (settings.hooks[hookType].length === 0) {
      delete settings.hooks[hookType];
    }
    if (Object.keys(settings.hooks).length === 0) {
      delete settings.hooks;
    }
  }

  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
}
```

- [ ] **Step 3: Update `registerHook()` to register both hooks**

Replace `registerHook()` (lines 457-465) with:

```javascript
function registerHook() {
  let spinner = step("Registering session hooks...");
  try {
    installHook("SessionStart", "rlm-session-start.js");
    installHook("SessionEnd", "rlm-session-end.js");
    spinner.succeed("Session hooks registered (start + end)");
  } catch (err) {
    spinner.warn("Hook registration failed: " + err.message);
  }
}
```

- [ ] **Step 4: Update `uninstall()` to remove both hooks**

Replace the uninstall hook section (lines 737-745) with:

```javascript
  // 2. Remove session hooks
  spinner = step("Removing session hooks...");
  try {
    uninstallHook("SessionStart", "rlm-session-start.js");
    uninstallHook("SessionEnd", "rlm-session-end.js");
    spinner.succeed("Session hooks removed");
  } catch (err) {
    spinner.warn("Hook removal failed: " + err.message);
  }
```

- [ ] **Step 5: Commit**

```bash
git add bin/cli.js
git commit -m "refactor: parameterize hook helpers for SessionStart + SessionEnd"
```

### Task 5: Add session-start hook status to `status()` command

**Files:**
- Modify: `bin/cli.js:810-903` (status function)

- [ ] **Step 1: Add hook status check after the "Installed" line**

After line 824 (`console.log(\`  Installed:   ${chalk.green("✔ Yes")}\`);`), add:

```javascript
  // Check session-start hook registration
  const settingsPath = path.join(CWD, ".claude", "settings.json");
  let hookRegistered = false;
  if (fs.existsSync(settingsPath)) {
    try {
      const settings = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
      hookRegistered = (settings.hooks?.SessionStart || []).some((entry) =>
        (entry.hooks || []).some((h) => h.command && h.command.includes("rlm-session-start"))
      );
    } catch {}
  }
  if (hookRegistered) {
    console.log(`  Start hook:  ${chalk.green("✔ Registered")}`);
  } else {
    console.log(`  Start hook:  ${chalk.yellow("✖ Missing")} ${chalk.dim("(run install to fix)")}`);
  }
```

- [ ] **Step 2: Verify status output**

```bash
node bin/cli.js status
```
Expected: New "Start hook" line appears between "Installed" and "Port"

- [ ] **Step 3: Commit**

```bash
git add bin/cli.js
git commit -m "feat: show session-start hook status in 'status' command"
```

### Task 6: Bump version to 2.1.0

**Files:**
- Modify: `package.json:2` (version field)
- Modify: `server/src/index.ts:252` (MCP server version)

- [ ] **Step 1: Update `package.json` version**

```json
// Before:
"version": "2.0.0",
// After:
"version": "2.1.0",
```

- [ ] **Step 2: Update MCP server version in `server/src/index.ts`**

```typescript
// Before (line 252):
  version: "2.0.0",
// After:
  version: "2.1.0",
```

- [ ] **Step 3: Rebuild MCP server**

```bash
cd server && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add package.json server/src/index.ts server/build/
git commit -m "chore: bump version to 2.1.0"
```
