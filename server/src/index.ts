#!/usr/bin/env node
/**
 * RLM Navigator MCP Server
 *
 * Exposes codebase navigation tools (get_status, rlm_tree, rlm_map, rlm_drill, rlm_search)
 * and REPL tools (rlm_repl_init, rlm_repl_exec, rlm_repl_status, rlm_repl_reset, rlm_repl_export)
 * that communicate with the Python daemon over TCP.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import * as net from "node:net";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, ChildProcess } from "node:child_process";

const DAEMON_HOST = "127.0.0.1";
const MAX_RESPONSE_CHARS = parseInt(process.env.RLM_MAX_RESPONSE || "8000", 10);

function resolveProjectRoot(): string {
  // 1. Explicit env var
  if (process.env.RLM_PROJECT_ROOT) return process.env.RLM_PROJECT_ROOT;

  // 2. Detect from script location: if running from <project>/.rlm/server/build/index.js,
  //    walk up to find the .rlm parent
  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const parts = scriptDir.split(path.sep);
  const rlmIdx = parts.lastIndexOf(".rlm");
  if (rlmIdx > 0) {
    return parts.slice(0, rlmIdx).join(path.sep);
  }

  // 3. Fallback
  return process.cwd();
}

const PROJECT_ROOT = resolveProjectRoot();

let daemonChild: ChildProcess | null = null;

function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0); // Signal 0 = existence check, doesn't kill
    return true;
  } catch {
    return false; // ESRCH = no such process
  }
}

function getDaemonPort(): number | null {
  // 1. Env var override
  if (process.env.RLM_DAEMON_PORT) {
    return parseInt(process.env.RLM_DAEMON_PORT, 10);
  }
  // 2. Read .rlm/port file
  try {
    const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
    const raw = fs.readFileSync(portFile, "utf-8").trim();

    // Try JSON format first (new), fall back to plain number (legacy)
    let port: number;
    let pid: number | null = null;
    try {
      const parsed = JSON.parse(raw);
      port = parsed.port;
      pid = parsed.pid || null;
    } catch {
      port = parseInt(raw, 10);
    }

    if (isNaN(port)) return null;

    // If we have a PID, check if it's still alive
    if (pid !== null && !isPidAlive(pid)) {
      // Stale port file — daemon is dead
      try { fs.unlinkSync(portFile); } catch {}
      return null;
    }

    return port;
  } catch {
    // File doesn't exist or unreadable — fall through
  }
  // 3. If .rlm/ exists, don't fall back to 9177 (would hit wrong daemon)
  const rlmDir = path.join(PROJECT_ROOT, ".rlm");
  if (fs.existsSync(rlmDir)) return null;
  // 4. Legacy mode (no .rlm/) — use default port
  return 9177;
}

function spawnDaemon(): void {
  if (daemonChild) return; // Already spawned

  const daemonScript = path.join(PROJECT_ROOT, ".rlm", "daemon", "rlm_daemon.py");
  if (!fs.existsSync(daemonScript)) return; // No .rlm install

  // Try python, then python3
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
}

async function waitForDaemon(maxWaitMs = 5000): Promise<boolean> {
  const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    await sleep(300);
    if (fs.existsSync(portFile)) {
      // Port file appeared — give daemon a moment to start accepting
      await sleep(200);
      return true;
    }
  }
  return false;
}

// Kill daemon child on exit if we spawned it
process.on("exit", () => {
  if (daemonChild && daemonChild.pid) {
    try {
      process.kill(daemonChild.pid);
    } catch {
      // Already dead
    }
  }
});

// ---------------------------------------------------------------------------
// Output truncation
// ---------------------------------------------------------------------------

function truncateResponse(text: string, maxChars: number = MAX_RESPONSE_CHARS): string {
  if (text.length <= maxChars) return text;
  const remaining = text.length - maxChars;
  const tokensEst = Math.round(remaining / 4);
  return text.slice(0, maxChars) + `\n... (truncated, ${remaining} more chars, ~${tokensEst} tokens)`;
}

// ---------------------------------------------------------------------------
// TCP client for daemon communication
// ---------------------------------------------------------------------------

function queryDaemon(request: object, timeoutMs = 10000): Promise<any> {
  return new Promise((resolve, reject) => {
    const port = getDaemonPort();
    if (port === null) {
      reject(Object.assign(new Error("No daemon running for this project"), { code: "ECONNREFUSED" }));
      return;
    }
    const client = new net.Socket();
    let data = Buffer.alloc(0);
    const timer = setTimeout(() => {
      client.destroy();
      reject(new Error("Daemon query timed out"));
    }, timeoutMs);

    client.connect(port, DAEMON_HOST, () => {
      client.write(JSON.stringify(request));
    });

    client.on("data", (chunk) => {
      data = Buffer.concat([data, chunk]);
    });

    client.on("end", () => {
      clearTimeout(timer);
      try {
        resolve(JSON.parse(data.toString("utf-8")));
      } catch {
        resolve({ raw: data.toString("utf-8") });
      }
    });

    client.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

let daemonRootValidatedAt = 0;
const VALIDATION_TTL_MS = 30_000; // Re-validate every 30s

async function validateDaemonRoot(): Promise<void> {
  if (Date.now() - daemonRootValidatedAt < VALIDATION_TTL_MS) return;
  try {
    const status = await queryDaemon({ action: "status" });
    if (status.root) {
      const daemonRoot = path.resolve(status.root);
      const expectedRoot = path.resolve(PROJECT_ROOT);
      if (daemonRoot !== expectedRoot) {
        // Stale port file pointing to wrong project's daemon — delete and respawn
        const portFile = path.join(PROJECT_ROOT, ".rlm", "port");
        try { fs.unlinkSync(portFile); } catch {}
        spawnDaemon();
        await waitForDaemon();
        // Verify the new daemon is correct
        const newStatus = await queryDaemon({ action: "status" });
        if (newStatus.root && path.resolve(newStatus.root) !== expectedRoot) {
          throw new Error(
            `Daemon root mismatch: expected ${expectedRoot}, got ${path.resolve(newStatus.root)}`
          );
        }
      }
    }
    daemonRootValidatedAt = Date.now();
  } catch (err: any) {
    if (err?.code === "ECONNREFUSED" || err?.message?.includes("ECONNREFUSED")) {
      // No daemon running — will be spawned on next query
      return;
    }
    throw err;
  }
}

async function queryDaemonWithRetry(request: object, timeoutMs = 10000, retries = 3): Promise<any> {
  await validateDaemonRoot();
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      return await queryDaemon(request, timeoutMs);
    } catch (err: any) {
      const isConnRefused = err?.code === "ECONNREFUSED" || err?.message?.includes("ECONNREFUSED");
      if (isConnRefused && attempt < retries - 1) {
        spawnDaemon();
        await waitForDaemon();
        continue;
      }
      throw err;
    }
  }
}

function checkHealth(): Promise<boolean> {
  return new Promise((resolve) => {
    const port = getDaemonPort();
    if (port === null) {
      resolve(false);
      return;
    }
    const client = new net.Socket();
    const timer = setTimeout(() => {
      client.destroy();
      resolve(false);
    }, 2000);

    client.connect(port, DAEMON_HOST, () => {
      // Daemon sends ALIVE on bare connection
      client.on("data", (chunk) => {
        clearTimeout(timer);
        const msg = chunk.toString("utf-8");
        client.destroy();
        resolve(msg.includes("ALIVE"));
      });
    });

    client.on("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

function readLines(
  filePath: string,
  startLine: number,
  endLine: number
): string {
  const content = fs.readFileSync(filePath, "utf-8");
  const lines = content.split("\n");
  // Lines are 1-indexed from the daemon
  const selected = lines.slice(startLine - 1, endLine);
  return selected
    .map((line, i) => `${(startLine + i).toString().padStart(4)} | ${line}`)
    .join("\n");
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "rlm-navigator",
  version: "2.0.0",
});

// --- get_status ---
server.tool(
  "get_status",
  "Check if the RLM daemon is running and responsive",
  {},
  async () => {
    try {
      const status = await queryDaemonWithRetry({ action: "status" });
      let text = `RLM daemon is ALIVE\nRoot: ${status.root}\nCached files: ${status.cache_size}\nLanguages: ${(status.languages || []).join(", ")}`;

      if (status.session) {
        const s = status.session;
        const mins = Math.floor(s.duration_s / 60);
        const secs = s.duration_s % 60;
        const duration = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        text += `\n\nSession Stats:`;
        text += `\n  Tool calls: ${s.tool_calls} | Duration: ${duration}`;
        text += `\n  Tokens served: ${s.tokens_served.toLocaleString()} | Tokens avoided: ${s.tokens_avoided.toLocaleString()} (${s.reduction_pct}% reduction)`;
        if (s.breakdown && Object.keys(s.breakdown).length > 0) {
          text += `\n  Breakdown:`;
          const actionMap: Record<string, string> = {
            squeeze: "rlm_map",
            find: "rlm_drill",
            search: "rlm_search",
            tree: "rlm_tree",
          };
          for (const [action, data] of Object.entries(s.breakdown) as [string, any][]) {
            const name = (actionMap[action] || action).padEnd(12);
            let line = `${data.calls} calls — ${data.tokens_served.toLocaleString()} served`;
            if (data.tokens_avoided) {
              line += `, ${data.tokens_avoided.toLocaleString()} avoided`;
            }
            text += `\n    ${name}${line}`;
          }
        }
      }

      return {
        content: [
          {
            type: "text" as const,
            text,
          },
        ],
      };
    } catch {
      return {
        content: [
          {
            type: "text" as const,
            text: "RLM daemon is OFFLINE. Start it with:\n  python daemon/rlm_daemon.py --root <project_path>",
          },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_tree ---
server.tool(
  "rlm_tree",
  "Get directory listing with file types and sizes (no file content). Use this instead of ls/find to see project structure.",
  {
    path: z
      .string()
      .default("")
      .describe("Directory path relative to project root (empty = root)"),
    max_depth: z
      .number()
      .int()
      .min(1)
      .max(10)
      .default(4)
      .describe("Maximum directory depth to traverse"),
  },
  async ({ path: dirPath, max_depth }) => {
    try {
      const result = await queryDaemonWithRetry({
        action: "tree",
        path: dirPath,
        max_depth,
      });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      return {
        content: [
          {
            type: "text" as const,
            text: truncateResponse(formatTree(result.tree, "")),
          },
        ],
      };
    } catch (err: any) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Daemon error: ${err.message}. Is the daemon running?`,
          },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_map ---
server.tool(
  "rlm_map",
  "Get structural skeleton of a file (signatures + docstrings only, no implementations). Use this instead of reading full files.",
  {
    path: z
      .string()
      .describe("File path relative to project root"),
  },
  async ({ path: filePath }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "squeeze", path: filePath });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      return {
        content: [{ type: "text" as const, text: truncateResponse(result.skeleton) }],
      };
    } catch (err: any) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Daemon error: ${err.message}. Is the daemon running?`,
          },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_drill ---
server.tool(
  "rlm_drill",
  "Surgically read only a specific symbol's implementation. First use rlm_map to find symbol names, then drill into the one you need.",
  {
    path: z
      .string()
      .describe("File path relative to project root"),
    symbol: z
      .string()
      .describe("Symbol name to drill into (function, class, method name)"),
  },
  async ({ path: filePath, symbol }) => {
    try {
      // Ask daemon for line range
      const findResult = await queryDaemonWithRetry({
        action: "find",
        path: filePath,
        symbol,
      });
      if (findResult.error) {
        return {
          content: [
            { type: "text" as const, text: `Error: ${findResult.error}` },
          ],
          isError: true,
        };
      }

      // Read those exact lines from the file
      // Resolve relative to daemon root via status
      const status = await queryDaemonWithRetry({ action: "status" });
      const absPath = path.resolve(status.root, filePath);
      const code = readLines(absPath, findResult.start_line, findResult.end_line);

      return {
        content: [
          {
            type: "text" as const,
            text: truncateResponse(
              `# ${symbol} in ${filePath} (L${findResult.start_line}-${findResult.end_line})\n\n${code}`
            ),
          },
        ],
      };
    } catch (err: any) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error: ${err.message}. Is the daemon running?`,
          },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_search ---
server.tool(
  "rlm_search",
  "Search for a symbol name across all files in a directory. Returns matching file paths and skeleton lines.",
  {
    query: z
      .string()
      .describe("Symbol or text to search for in file skeletons"),
    path: z
      .string()
      .default("")
      .describe(
        "Directory path relative to project root to search in (empty = root)"
      ),
  },
  async ({ query, path: dirPath }) => {
    try {
      const result = await queryDaemonWithRetry({
        action: "search",
        query,
        path: dirPath,
      });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }

      if (!result.results || result.results.length === 0) {
        return {
          content: [
            {
              type: "text" as const,
              text: `No matches found for "${query}" in ${dirPath || "project root"}`,
            },
          ],
        };
      }

      const output = result.results
        .map(
          (r: any) =>
            `## ${r.path}\n${r.matches.map((m: string) => `  ${m}`).join("\n")}`
        )
        .join("\n\n");

      return {
        content: [
          {
            type: "text" as const,
            text: truncateResponse(
              `Found "${query}" in ${result.results.length} file(s):\n\n${output}`
            ),
          },
        ],
      };
    } catch (err: any) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error: ${err.message}. Is the daemon running?`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Chunk Tools
// ---------------------------------------------------------------------------

// --- rlm_chunks ---
server.tool(
  "rlm_chunks",
  "Get chunk metadata for a file. Returns total chunks, line count, and chunk parameters. Use before rlm_chunk to know how many chunks exist.",
  {
    path: z
      .string()
      .describe("File path relative to project root"),
  },
  async ({ path: filePath }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "chunks_list", path: filePath });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      if (result.status === "pending") {
        return {
          content: [{ type: "text" as const, text: `Chunks for ${filePath} are still being generated. Try again shortly.` }],
        };
      }
      const m = result.manifest;
      return {
        content: [{
          type: "text" as const,
          text: `${filePath}: ${m.total_chunks} chunks (${m.total_lines} lines, chunk_size=${m.chunk_size}, overlap=${m.overlap})`,
        }],
      };
    } catch (err: any) {
      return {
        content: [{ type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` }],
        isError: true,
      };
    }
  }
);

// --- rlm_chunk ---
server.tool(
  "rlm_chunk",
  "Read a specific chunk of a file by index. Use rlm_chunks first to see how many chunks exist.",
  {
    path: z
      .string()
      .describe("File path relative to project root"),
    chunk: z
      .number()
      .int()
      .min(0)
      .describe("Chunk index (0-based)"),
  },
  async ({ path: filePath, chunk: chunkIdx }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "chunks_read", path: filePath, chunk: chunkIdx });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      const header = `# ${filePath} chunk ${result.chunk}/${result.total_chunks - 1} (lines ${result.lines})\n\n`;
      return {
        content: [{ type: "text" as const, text: truncateResponse(header + result.content) }],
      };
    } catch (err: any) {
      return {
        content: [{ type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` }],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// REPL Tools
// ---------------------------------------------------------------------------

// --- rlm_repl_init ---
server.tool(
  "rlm_repl_init",
  "Initialize the stateful Python REPL. Clears any existing state and creates a fresh environment.",
  {},
  async () => {
    try {
      const result = await queryDaemonWithRetry({ action: "repl_init" });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      return {
        content: [
          {
            type: "text" as const,
            text: "REPL initialized. Helpers available: peek(), grep(), chunk_indices(), write_chunks(), add_buffer()",
          },
        ],
      };
    } catch (err: any) {
      return {
        content: [
          { type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_repl_exec ---
server.tool(
  "rlm_repl_exec",
  `Execute Python code in the stateful REPL. Variables persist across calls. Built-in helpers:
- peek(file_path, start=1, end=None) — read lines from file relative to root
- grep(pattern, path=".", max_results=50) — regex search across files
- chunk_indices(file_path, size=200, overlap=20) — compute chunk boundaries
- write_chunks(file_path, out_dir=None, size=200, overlap=20) — write chunks to disk
- add_buffer(key, text) — accumulate findings in named buffers`,
  {
    code: z.string().describe("Python code to execute in the REPL"),
  },
  async ({ code }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "repl_exec", code });
      if (result.error && !result.output) {
        return {
          content: [{ type: "text" as const, text: `Error:\n${result.error}` }],
          isError: true,
        };
      }
      let text = "";
      if (result.output) text += result.output;
      if (result.error) text += `\nError:\n${result.error}`;
      if (result.variables && result.variables.length > 0) {
        text += `\nVariables: ${result.variables.join(", ")}`;
      }
      if (result.staleness_warning) {
        text += formatStalenessWarning(result.staleness_warning);
      }
      return {
        content: [{ type: "text" as const, text: truncateResponse(text.trim()) }],
      };
    } catch (err: any) {
      return {
        content: [
          { type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_repl_status ---
server.tool(
  "rlm_repl_status",
  "Check the current state of the REPL — variables, buffer counts, execution count.",
  {},
  async () => {
    try {
      const result = await queryDaemonWithRetry({ action: "repl_status" });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      const lines = [
        `Variables: ${(result.variables || []).join(", ") || "(none)"}`,
        `Buffers: ${JSON.stringify(result.buffer_count || {})}`,
        `Exec count: ${result.exec_count || 0}`,
      ];
      if (result.staleness) {
        lines.push(formatStalenessWarning(result.staleness));
      }
      return {
        content: [{ type: "text" as const, text: lines.join("\n") }],
      };
    } catch (err: any) {
      return {
        content: [
          { type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_repl_reset ---
server.tool(
  "rlm_repl_reset",
  "Clear all REPL state — variables, buffers, execution history.",
  {},
  async () => {
    try {
      const result = await queryDaemonWithRetry({ action: "repl_reset" });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      return {
        content: [{ type: "text" as const, text: "REPL state cleared." }],
      };
    } catch (err: any) {
      return {
        content: [
          { type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` },
        ],
        isError: true,
      };
    }
  }
);

// --- rlm_repl_export ---
server.tool(
  "rlm_repl_export",
  "Export all accumulated buffers from the REPL. Use after add_buffer() calls to retrieve collected findings.",
  {},
  async () => {
    try {
      const result = await queryDaemonWithRetry({ action: "repl_export_buffers" });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      const buffers = result.buffers || {};
      if (Object.keys(buffers).length === 0) {
        return {
          content: [{ type: "text" as const, text: "No buffers accumulated." }],
        };
      }
      const output = Object.entries(buffers)
        .map(([key, texts]: [string, any]) => {
          return `## ${key} (${texts.length} entries)\n${texts.map((t: string, i: number) => `[${i}] ${t}`).join("\n")}`;
        })
        .join("\n\n");
      return {
        content: [{ type: "text" as const, text: truncateResponse(output) }],
      };
    } catch (err: any) {
      return {
        content: [
          { type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatStalenessWarning(staleness: any): string {
  const lines: string[] = ["\n⚠ STALE DATA WARNING:"];
  if (staleness.variables) {
    for (const [varName, files] of Object.entries(staleness.variables) as [string, any[]][]) {
      const fileList = files.map((f: any) => `${f.file} (${f.reason})`).join(", ");
      lines.push(`  var '${varName}': ${fileList}`);
    }
  }
  if (staleness.buffers) {
    for (const [bufName, files] of Object.entries(staleness.buffers) as [string, any[]][]) {
      const fileList = files.map((f: any) => `${f.file} (${f.reason})`).join(", ");
      lines.push(`  buffer '${bufName}': ${fileList}`);
    }
  }
  return lines.join("\n");
}

function formatTree(entries: any[], indent: string): string {
  const lines: string[] = [];
  for (const entry of entries) {
    if (entry.type === "dir") {
      const childCount =
        typeof entry.children === "number"
          ? entry.children
          : entry.entries?.length ?? 0;
      lines.push(`${indent}${entry.name}/ (${childCount} items)`);
      if (entry.entries) {
        lines.push(formatTree(entry.entries, indent + "  "));
      }
    } else {
      const size = formatSize(entry.size || 0);
      const lang = entry.language ? ` [${entry.language}]` : "";
      lines.push(`${indent}${entry.name} (${size})${lang}`);
    }
  }
  return lines.join("\n");
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("MCP server error:", err);
  process.exit(1);
});
