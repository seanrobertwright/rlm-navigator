# Daemon Resilience — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the daemon persistent and ensure the MCP server auto-recovers from all connection failures.

**Architecture:** Three changes: (1) disable idle timeout so daemon stays alive, (2) extend `isConnectionError()` to match timeout-class errors so retries trigger respawn, (3) clean stale state before respawn and add a minimal fallback note to the skill.

**Tech Stack:** TypeScript (vitest), MCP server config.

---

### Task 1: Extend `isConnectionError()` to match timeout errors

**Files:**
- Modify: `server/src/utils.ts:188-192`
- Test: `server/tests/utils.test.ts:428-449`

**Step 1: Write the failing tests**

Add to the `isConnectionError` describe block in `server/tests/utils.test.ts`, after the `"returns false for other errors"` test (line 448):

```typescript
  test("returns true for ETIMEDOUT", () => {
    const err = Object.assign(new Error("timed out"), { code: "ETIMEDOUT" });
    expect(isConnectionError(err)).toBe(true);
  });

  test("returns true for ECONNABORTED", () => {
    const err = Object.assign(new Error("aborted"), { code: "ECONNABORTED" });
    expect(isConnectionError(err)).toBe(true);
  });

  test("returns true for timeout message without error code", () => {
    const err = new Error("Daemon query timed out");
    expect(isConnectionError(err)).toBe(true);
  });
```

**Step 2: Run tests to verify they fail**

Run: `cd server && npx vitest run tests/utils.test.ts`
Expected: 3 new tests FAIL — `isConnectionError` returns `false` for all three.

**Step 3: Implement the change**

In `server/src/utils.ts`, replace lines 188-192:

```typescript
export function isConnectionError(err: unknown): err is NodeJS.ErrnoException {
  if (!(err instanceof Error)) return false;
  const code = (err as NodeJS.ErrnoException).code;
  return code === "ECONNREFUSED" || code === "ECONNRESET" || code === "EPIPE";
}
```

With:

```typescript
export function isConnectionError(err: unknown): err is NodeJS.ErrnoException {
  if (!(err instanceof Error)) return false;
  const code = (err as NodeJS.ErrnoException).code;
  return (
    code === "ECONNREFUSED" ||
    code === "ECONNRESET" ||
    code === "EPIPE" ||
    code === "ETIMEDOUT" ||
    code === "ECONNABORTED" ||
    err.message.includes("timed out")
  );
}
```

**Step 4: Run tests to verify they pass**

Run: `cd server && npx vitest run tests/utils.test.ts`
Expected: All tests PASS (existing + 3 new).

**Step 5: Commit**

```bash
git add server/src/utils.ts server/tests/utils.test.ts
git commit -m "feat: extend isConnectionError to match timeout errors"
```

---

### Task 2: Remove idle timeout and fix respawn in `index.ts`

**Files:**
- Modify: `server/src/index.ts:76,193-210`

**Step 1: Change idle timeout from 300 to 0**

In `server/src/index.ts` line 76, change:

```typescript
      const child = spawn(cmd, [daemonScript, "--root", PROJECT_ROOT, "--idle-timeout", "300"], {
```

To:

```typescript
      const child = spawn(cmd, [daemonScript, "--root", PROJECT_ROOT, "--idle-timeout", "0"], {
```

**Step 2: Add stale state cleanup before respawn**

In `server/src/index.ts`, replace the retry block in `queryDaemonWithRetry()` (lines 199-205):

```typescript
      if (isConnectionError(err) && attempt < retries - 1) {
        spawnDaemon();
        const ok = await waitForDaemon();
        if (!ok) {
          throw new Error("Failed to start daemon after spawn attempt");
        }
        continue;
      }
```

With:

```typescript
      if (isConnectionError(err) && attempt < retries - 1) {
        // Clean stale state so spawnDaemon() doesn't short-circuit
        const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
        try { fs.unlinkSync(portFile); } catch {}
        daemonChild = null;
        spawning = false;
        spawnDaemon();
        const ok = await waitForDaemon();
        if (!ok) {
          throw new Error("Failed to start daemon after spawn attempt");
        }
        continue;
      }
```

**Step 3: Build to verify no syntax errors**

Run: `cd server && npm run build`
Expected: Clean build, no errors.

**Step 4: Commit**

```bash
git add server/src/index.ts
git commit -m "feat: persistent daemon and stale state cleanup on respawn"
```

---

### Task 3: Add fallback note to SKILL.md

**Files:**
- Modify: `.claude/skills/rlm-navigator/SKILL.md:11-15`

**Step 1: Add recovery guidance**

In `.claude/skills/rlm-navigator/SKILL.md`, replace lines 11-15:

```markdown
If offline, tell the user to start the daemon:
```
python daemon/rlm_daemon.py --root <project_path>
```
```

With:

```markdown
If offline or tools return errors, call `get_status` — the MCP server will auto-restart the daemon.
```

**Step 2: Commit**

```bash
git add .claude/skills/rlm-navigator/SKILL.md
git commit -m "docs: add daemon auto-recovery note to skill"
```

---

### Task 4: Full verification

**Files:** None (verification only)

**Step 1: Run all MCP server tests**

Run: `cd server && npx vitest run`
Expected: All tests PASS.

**Step 2: Build MCP server**

Run: `cd server && npm run build`
Expected: Clean build, no errors.

**Step 3: Run all daemon tests**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS (206+).

**Step 4: Verify commit history**

```bash
git log --oneline -4
```

Expected: 3 commits from tasks 1-3 plus design doc.
