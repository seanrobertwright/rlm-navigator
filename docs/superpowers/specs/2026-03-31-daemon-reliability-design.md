# Daemon Reliability: Session-Start Hook + Retry Hardening

**Date:** 2026-03-31
**Version:** 2.1.0
**Status:** Approved

## Problem

The RLM daemon frequently fails to start at session begin with:
```
Error: Daemon error: Failed to start daemon after spawn attempt. Is the daemon running?
```

Root causes:
- Stale `.rlm/port` and `.rlm/daemon.lock` files from unclean session exits
- No proactive daemon startup — current lazy-spawn on first tool call is timing-sensitive
- Silent failures with no diagnostics when spawn fails
- 10s timeout in `waitForDaemon()` can be too short on Windows

## Solution

Three coordinated changes:

1. **Session-start hook** — proactively clean stale state and spawn the daemon before any tool call
2. **MCP server retry hardening** — better timeouts, diagnostics, and error messages
3. **Install integration** — register/unregister the hook, show status

## Design

### 1. Session-Start Hook (`hooks/rlm-session-start.js`)

**Trigger:** `SessionStart` event in `.claude/settings.json`

**Execution flow:**

#### Step 1: Locate `.rlm/`
Walk up from `cwd` to find the nearest `.rlm/` directory (same pattern as `rlm-session-end.js`).
If not found, exit 0 silently (not an RLM project).

#### Step 2: Stale File Cleanup
Read `.rlm/port` (JSON: `{ port, pid }`) and `.rlm/daemon.lock` (JSON: `{ pid, port, root, started_at }`). For each, parse the PID and check liveness using `process.kill(pid, 0)` (cross-platform Node.js pattern, matching existing `isPidAlive()` in `bin/cli.js`).

If PID is dead:
- Delete `.rlm/port` and `.rlm/daemon.lock`
- Log: `"[RLM] Cleaned stale daemon state (PID <pid> no longer running)"`

If PID is alive:
- Verify daemon is responsive via bare TCP health check (2s timeout)
- If responsive: log `"[RLM] Daemon already running on port <port>"`, exit 0
- If unresponsive: force-kill the stale process, delete both files, proceed to spawn
  - Windows: `execSync('taskkill /PID <pid> /F')` (matching existing `forceKill()` in `hooks/rlm-session-end.js`)
  - Unix: `process.kill(pid, 'SIGKILL')`

#### Step 3: Pre-flight Checks
Before spawning, verify:
- `.rlm/daemon/rlm_daemon.py` exists
- Python is available: try `python --version`, fall back to `python3 --version`

If either fails:
- Log actionable error: `"[RLM] Cannot start daemon: <reason>. Run 'npx rlm-navigator status' to diagnose."`
- Exit 0 (don't block session)

#### Step 4: Spawn Daemon
Redirect stderr to a temp file (avoids SIGPIPE issues when the hook exits before the detached daemon):
```javascript
const stderrFd = fs.openSync(path.join(rlmDir, 'daemon-start.log'), 'w');
const child = spawn(pythonCmd, [daemonScript, "--root", projectRoot, "--idle-timeout", "0"], {
  detached: true,
  stdio: ["ignore", "ignore", stderrFd],
});
child.unref();
fs.closeSync(stderrFd);
```

If the daemon fails to start during the wait period, read `daemon-start.log` for diagnostics.

#### Step 5: Wait and Verify
- Poll for `.rlm/port` file: 300ms interval, 15s timeout
- Once port file appears, bare TCP health check (2s timeout)
- Success: log `"[RLM] Daemon started on port <port> (PID <pid>)"`
- Failure: log `"[RLM] Failed to start daemon. <diagnostics>. Run 'npx rlm-navigator status' to diagnose."`

**Exit behavior:** Always exits 0. Hook failures log diagnostics but never block the session.

### 2. MCP Server Retry Hardening (`server/src/index.ts`)

#### `waitForDaemon()` Changes
- Increase port-file poll timeout from 10s to 20s
- Log each phase: `"Waiting for port file..."`, `"Port file found, verifying root..."`

#### `spawnDaemon()` Changes
- Redirect stderr to `.rlm/daemon-start.log` via file descriptor (same pattern as hook — avoids pipe lifecycle issues with detached+unref'd child)
- Add module-level `daemonStderrPath` variable pointing to the log file so `queryDaemonWithRetry()` can read it for diagnostics
- If spawn fails for both `python` and `python3`, write the error messages to the same log file

#### `queryDaemonWithRetry()` Changes
- On final failure, gather diagnostics before throwing:
  - `daemon_script`: exists / missing
  - `python`: found / not found (and which command)
  - `port_file`: exists (contents) / missing
  - `lock_file`: exists (PID alive/dead) / missing
  - `spawn_stderr`: last 1KB if available
- Error message format:
  ```
  Daemon error: Failed to start daemon after spawn attempt.
  Diagnostics: python=found(python3), daemon_script=exists, port_file=missing, lock_file=stale(pid=12345)
  Spawn stderr: <last few lines>
  Run 'npx rlm-navigator status' to diagnose further.
  ```

### 3. Install Integration (`bin/cli.js`)

#### `install()` Changes
- Copy `hooks/rlm-session-start.js` to `.rlm/hooks/`
- Register `SessionStart` hook in `.claude/settings.json` using the existing nested format (matching `installHook()` pattern in `bin/cli.js`):
  ```json
  {
    "hooks": {
      "SessionStart": [
        { "hooks": [{ "type": "command", "command": "node .rlm/hooks/rlm-session-start.js" }] }
      ],
      "SessionEnd": [
        { "hooks": [{ "type": "command", "command": "node .rlm/hooks/rlm-session-end.js" }] }
      ]
    }
  }
  ```
- Refactor `installHook()` and `uninstallHook()` into parameterized helpers `installHook(hookType, hookFilename)` and `uninstallHook(hookType, hookFilename)` to handle both `SessionStart` and `SessionEnd` without duplication
- Hook command paths use absolute paths with forward-slash normalization (matching existing `installHook()` pattern at `bin/cli.js:569`)

#### `uninstall()` Changes
- Remove `SessionStart` hook entry from `.claude/settings.json`
- Remove copied `hooks/rlm-session-start.js` from `.rlm/hooks/` (parity with session-end cleanup)

#### `status()` Changes
- Add line: `"Session-start hook: registered"` or `"Session-start hook: missing (run install to fix)"`
- Check by reading `.claude/settings.json` for the `SessionStart` hook entry

## Shared Patterns

Patterns shared between session-start and session-end hooks:
- `.rlm/` directory discovery (walk up from cwd)
- Port/lock file reading (JSON parsing)
- TCP communication (socket connect, send, receive)

Patterns new to session-start hook only:
- PID liveness checking (`process.kill(pid, 0)`)
- Stale file cleanup before spawn
- Pre-flight checks (Python availability, daemon script existence)
- Proactive daemon spawn and health verification

Each hook is self-contained (no shared module — only two consumers).

## Failure Philosophy

- **Hook always exits 0** — never blocks a session start
- **All failures logged with actionable diagnostics** — user can self-diagnose
- **MCP server is the safety net** — if hook fails or isn't installed, existing lazy-spawn still works
- **MCP server errors now self-diagnose** — enough context in error messages to understand what went wrong
- **No silent failures** — every failure path produces a log message

## Files Changed

| File | Change Type | Description |
|------|-------------|-------------|
| `hooks/rlm-session-start.js` | New | Session-start hook with stale cleanup + proactive spawn |
| `server/src/index.ts` | Modified | Timeout increase, stderr capture, diagnostic error messages |
| `bin/cli.js` | Modified | Register/unregister SessionStart hook, show hook status |

## Version

Bump to 2.1.0 (minor feature addition).
