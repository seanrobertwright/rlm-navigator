#!/usr/bin/env node
"use strict";

/**
 * Claude Code SessionEnd hook — prints RLM Navigator session stats.
 *
 * Strategy:
 *   1. Try TCP connection to running daemon (via .rlm/port)
 *   2. Fallback: read last line of .rlm/sessions.jsonl
 *
 * Silent on any error. Always exits 0.
 */

const fs = require("fs");
const path = require("path");
const net = require("net");

function findRlmDir() {
  let dir = process.cwd();
  while (true) {
    const candidate = path.join(dir, ".rlm");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
      return candidate;
    }
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function formatNumber(n) {
  return n.toLocaleString("en-US");
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

function printSummary(data) {
  const session = data.session || data;
  const calls = session.tool_calls || 0;
  if (calls === 0) return;

  const served = session.tokens_served || 0;
  const avoided = session.tokens_avoided || 0;
  const reduction = session.reduction_percent || 0;
  const elapsed = session.uptime_seconds || session.elapsed_seconds || 0;

  console.log(
    `\u{1F4CA} RLM Session: ${formatNumber(calls)} calls | ` +
    `${formatNumber(served)} tokens served | ` +
    `${formatNumber(avoided)} avoided (${reduction}% reduction) | ` +
    formatDuration(elapsed)
  );
}

function tryDaemon(rlmDir) {
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
    const client = new net.Socket();
    let buf = "";

    const timer = setTimeout(() => {
      client.destroy();
      resolve(false);
    }, 2000);

    client.connect(port, "127.0.0.1", () => {
      client.write(JSON.stringify({ action: "status" }));
    });

    client.on("data", (chunk) => {
      buf += chunk.toString("utf-8");
    });

    client.on("end", () => {
      clearTimeout(timer);
      try {
        const resp = JSON.parse(buf);
        if (resp.session) {
          printSummary(resp);
          resolve(true);
        } else {
          resolve(false);
        }
      } catch {
        resolve(false);
      }
    });

    client.on("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

function tryLogFile(rlmDir) {
  const logFile = path.join(rlmDir, "sessions.jsonl");
  if (!fs.existsSync(logFile)) return false;

  try {
    const content = fs.readFileSync(logFile, "utf-8").trimEnd();
    const lines = content.split("\n");
    const last = JSON.parse(lines[lines.length - 1]);

    // Only show if recent (within last 5 minutes)
    if (last.timestamp) {
      const logTime = new Date(last.timestamp).getTime();
      const now = Date.now();
      if (now - logTime > 5 * 60 * 1000) return false;
    }

    printSummary(last);
    return true;
  } catch {
    return false;
  }
}

async function main() {
  try {
    const rlmDir = findRlmDir();
    if (!rlmDir) return;

    const gotDaemon = await tryDaemon(rlmDir);
    if (!gotDaemon) {
      tryLogFile(rlmDir);
    }
  } catch {
    // Silent failure
  }
}

main();
