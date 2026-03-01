# Daemon Resilience — Design Document

**Date**: 2026-03-01
**Status**: Approved

## Problem

The daemon shuts down after 5 minutes of inactivity (`--idle-timeout 300`). When it stops, the MCP server's retry logic only handles `ECONNREFUSED`, `ECONNRESET`, and `EPIPE` — not timeouts. This means timeout errors (from a dead daemon whose port file is stale) are thrown to the tool caller instead of triggering a respawn. The skill has no fallback guidance when daemon tools fail.

## Solution

Three targeted fixes:

1. **Remove idle timeout** — daemon runs persistently until the MCP server process ends
2. **Extend retry coverage** — `isConnectionError()` also matches timeout-class errors, and stale port files are cleaned before respawn
3. **Add minimal skill fallback** — one line telling the AI to check daemon status if tools fail

## Changes

### 1. Remove Idle Timeout

In `server/src/index.ts`, change `spawnDaemon()` args from:

```javascript
spawn(cmd, [daemonScript, "--root", PROJECT_ROOT, "--idle-timeout", "300"], { ... })
```

To:

```javascript
spawn(cmd, [daemonScript, "--root", PROJECT_ROOT, "--idle-timeout", "0"], { ... })
```

`--idle-timeout 0` disables the timeout entirely. The daemon stays alive as long as the MCP server session is active.

### 2. Extend Retry Logic

**`server/src/utils.ts` — `isConnectionError()`**

Currently only matches:

```typescript
code === "ECONNREFUSED" || code === "ECONNRESET" || code === "EPIPE"
```

Add timeout-class errors:

```typescript
code === "ECONNREFUSED" || code === "ECONNRESET" || code === "EPIPE"
|| code === "ETIMEDOUT" || code === "ECONNABORTED"
|| (err.message && err.message.includes("timed out"))
```

The last condition catches the daemon's own `"Daemon query timed out"` error thrown by the `queryDaemon()` timer in utils.ts:280.

**`server/src/index.ts` — `queryDaemonWithRetry()`**

Before calling `spawnDaemon()`, delete the stale port file so `waitForDaemon()` correctly waits for a fresh one:

```typescript
if (isConnectionError(err) && attempt < retries - 1) {
  // Clean stale port file before respawn
  const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
  try { fs.unlinkSync(portFile); } catch {}
  daemonChild = null;
  spawning = false;
  spawnDaemon();
  const ok = await waitForDaemon();
  ...
}
```

Also reset `daemonChild` and `spawning` flags so `spawnDaemon()` doesn't short-circuit when the old process is dead.

### 3. Minimal Skill Fallback

Add to `.claude/skills/rlm-navigator/SKILL.md` after the tool list:

```
> If daemon tools return errors, run `get_status` to check daemon health. The MCP server will auto-restart it.
```

This gives the AI a recovery action without adding complex fallback logic to the skill.

## Files Changed

| File | Change |
|------|--------|
| `server/src/index.ts` | `--idle-timeout 0` in `spawnDaemon()`, stale port cleanup + flag reset in `queryDaemonWithRetry()` |
| `server/src/utils.ts` | Extend `isConnectionError()` to match ETIMEDOUT, ECONNABORTED, "timed out" |
| `.claude/skills/rlm-navigator/SKILL.md` | Add 1-line fallback note |

No new dependencies. No daemon code changes — the daemon already supports `--idle-timeout 0`.
