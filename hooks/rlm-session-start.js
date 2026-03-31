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
