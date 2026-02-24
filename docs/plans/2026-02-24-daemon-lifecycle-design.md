# Daemon Lifecycle Management — Design

## Problem

Daemons are spawned but never properly managed, causing:
- Orphaned processes after Claude Code exits or crashes
- Stale port files pointing to dead daemons
- Wrong-project connections when port files are stale
- Multiple daemons per project from retry-spawning
- No cleanup on uninstall

Root cause: no process lock, no shutdown protocol, no spawn verification.

## Approach

Add a lock file, graceful shutdown protocol, spawn verification, and CLI cleanup — without changing the core architecture.

## 1. Lock File

**File**: `.rlm/daemon.lock` (JSON)

```json
{
  "pid": 12345,
  "port": 9177,
  "root": "/path/to/project",
  "started_at": "2026-02-24T10:30:00Z"
}
```

**Startup behavior:**
- Write lock file before port file
- If lock exists and PID alive and root matches → refuse to start
- If lock exists and PID dead → clean stale lock + port file, proceed
- Lock file removed alongside port file on shutdown

Port file kept for backward compatibility. Lock file is daemon-internal.

**Files changed:** `daemon/rlm_daemon.py`

## 2. Graceful Shutdown Protocol

**New daemon action:** `{"action": "shutdown"}`

**Shutdown sequence:**
1. Flush session stats to `sessions.jsonl`
2. Save REPL state (pickle)
3. Close TCP listener
4. Remove port file
5. Remove lock file
6. Exit process

**Callers:**
- Session-end hook: reads port from `.rlm/port`, sends shutdown, waits 3s, force-kills PID if no response
- CLI uninstall: same shutdown + force-kill before removing `.rlm/`
- CLI install (reinstall): shutdown existing daemon before installing new version

**Force-kill fallback:** If graceful shutdown fails, kill by PID from lock file. Windows: `taskkill /PID`. Unix: `kill -9`.

**Files changed:** `daemon/rlm_daemon.py`, `hooks/rlm-session-end.js`, `bin/cli.js`

## 3. Spawn Verification & Retry Hardening

**Spawn verification:** After `waitForDaemon()` sees port file, send TCP health check (`{"action": "status"}`) and verify response `root` matches project. Only then consider spawn successful.

**Single spawn guard:** Boolean flag prevents concurrent spawns during retries.

**Spawn timeout:** 10 seconds max. If daemon not responsive, log error and stop retrying.

**Root validation:** Validate on first connection per session (remove 30-second TTL). Trust after first validation.

**Files changed:** `server/src/index.ts`

## 4. CLI Cleanup

**Install (reinstall):**
1. Check `.rlm/daemon.lock` for running daemon
2. Send shutdown → wait 3s → force-kill
3. Remove stale `.rlm/` contents
4. Fresh install

**Uninstall:**
1. Read lock file for PID
2. Shutdown → force-kill fallback
3. Remove `.rlm/`, MCP registration, hook

**Status enhancement:**
- Read lock file, validate PID alive
- TCP health check
- Show: PID, port, project root, uptime, health status
- Auto-clean orphaned state

**Files changed:** `bin/cli.js`

## Failure Modes Addressed

| # | Issue | Fix |
|---|---|---|
| 1 | Session-end hook doesn't shut down daemon | Shutdown action in hook |
| 2 | Port file not cleaned on crash | Lock file with PID validation on all reads |
| 3 | Uninstall doesn't kill daemon | CLI sends shutdown before removal |
| 4 | Wrong-project connection | Root validation on first connection |
| 5 | Spawn failures silently ignored | Spawn verification with health check |
| 6 | Multiple spawns during retries | Single spawn guard flag |
| 7 | Install doesn't kill existing daemon | Shutdown before reinstall |
| 8 | No maximum daemon lifetime | Idle timeout already exists; lock file makes cleanup reliable |

## Files to Modify

| File | Changes |
|---|---|
| `daemon/rlm_daemon.py` | Lock file write/read/cleanup, shutdown action handler |
| `server/src/index.ts` | Spawn verification, single spawn guard, root validation on first connection |
| `bin/cli.js` | Shutdown before install/uninstall, enhanced status |
| `hooks/rlm-session-end.js` | Send shutdown signal, force-kill fallback |
