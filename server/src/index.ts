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
import {
  truncateResponse,
  formatSize,
  formatTree,
  formatStats,
  formatStalenessWarning,
  readLines,
  isPidAlive,
  formatProgressMessage,
} from "./utils.js";

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

async function queryDaemonWithRetry(request: object, timeoutMs = 10000, retries = 3): Promise<any> {
  await validateDaemonRoot();
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      return await queryDaemon(request, timeoutMs);
    } catch (err: any) {
      const isConnRefused = err?.code === "ECONNREFUSED" || err?.message?.includes("ECONNREFUSED");
      if (isConnRefused && attempt < retries - 1) {
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
        if (status.session?.progress_summary) {
          const p = status.session.progress_summary;
          const total = p.sub_agent_dispatches;
          if (total > 0) {
            text += `\n\nSub-agent Activity:`;
            text += `\n  Dispatches: ${total} (${p.analyses} chunk analysis, ${p.enrichments} enrichment)`;
            text += `\n  Chunks analyzed: ${p.chunks_analyzed} | Answers found: ${p.answers_found}`;
            if (status.session.progress_last_event) {
              const last = status.session.progress_last_event;
              const lastMsg = formatProgressMessage(last.event, last.details || {});
              text += `\n  Last: ${lastMsg}`;
            }
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
            text: truncateResponse(formatTree(result.tree, "")) + formatStats(result),
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
        content: [{ type: "text" as const, text: truncateResponse(result.skeleton) + formatStats(result) }],
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
            ) + formatStats(findResult),
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
            ) + formatStats(result),
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
// Document Navigation Tools
// ---------------------------------------------------------------------------

// --- rlm_doc_map ---
server.tool(
  "rlm_doc_map",
  "Get hierarchical outline of a document file (.md, .pdf, .txt, .rst). Returns section tree with titles and line ranges.",
  {
    path: z.string().describe("Document file path relative to project root"),
  },
  async ({ path: filePath }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "doc_map", path: filePath });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      const treeText = JSON.stringify(result.tree, null, 2);
      return {
        content: [{ type: "text" as const, text: truncateResponse(treeText) + formatStats(result) }],
      };
    } catch (err: any) {
      return {
        content: [{ type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` }],
        isError: true,
      };
    }
  }
);

// --- rlm_doc_drill ---
server.tool(
  "rlm_doc_drill",
  "Extract a specific section from a document file by section title. Use rlm_doc_map first to see available sections.",
  {
    path: z.string().describe("Document file path relative to project root"),
    section: z.string().describe("Section title to extract (from rlm_doc_map output)"),
  },
  async ({ path: filePath, section }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "doc_drill", path: filePath, section });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      return {
        content: [{ type: "text" as const, text: truncateResponse(result.content) + formatStats(result) }],
      };
    } catch (err: any) {
      return {
        content: [{ type: "text" as const, text: `Daemon error: ${err.message}. Is the daemon running?` }],
        isError: true,
      };
    }
  }
);

// --- rlm_assess ---
server.tool(
  "rlm_assess",
  "Assess whether accumulated context is sufficient to answer a query. Call after gathering code/doc snippets to decide whether to continue navigating or synthesize an answer.",
  {
    query: z.string().describe("The original user question"),
    context_summary: z.string().describe("Brief summary of what has been found so far"),
  },
  async ({ query, context_summary }) => {
    try {
      const result = await queryDaemonWithRetry({ action: "assess", query, context_summary });
      if (result.error) {
        return {
          content: [{ type: "text" as const, text: `Error: ${result.error}` }],
          isError: true,
        };
      }
      return {
        content: [{ type: "text" as const, text: result.assessment + formatStats(result) }],
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
          text: `${filePath}: ${m.total_chunks} chunks (${m.total_lines} lines, chunk_size=${m.chunk_size}, overlap=${m.overlap})` + formatStats(result),
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
        content: [{ type: "text" as const, text: truncateResponse(header + result.content) + formatStats(result) }],
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
// Progress Tracking
// ---------------------------------------------------------------------------

// --- rlm_progress ---
server.tool(
  "rlm_progress",
  "Report sub-agent progress. Call this at each phase boundary during chunk-delegate-synthesize and enrichment workflows to provide visual feedback.",
  {
    event: z
      .enum([
        "chunking_start", "chunking_complete", "chunk_dispatch",
        "chunk_complete", "synthesis_start", "synthesis_complete",
        "answer_found", "queries_suggested",
      ])
      .describe("The progress event type"),
    details: z
      .object({
        file: z.string().optional().describe("File being processed"),
        agent: z.string().optional().describe("Sub-agent name (rlm-subcall or rlm-enricher)"),
        chunk: z.number().int().optional().describe("Current chunk index (0-based)"),
        total_chunks: z.number().int().optional().describe("Total chunks in batch"),
        query: z.string().optional().describe("User query being investigated"),
        count: z.number().int().optional().describe("Number of items (symbols, queries, etc.)"),
        summary: z.string().optional().describe("Brief result summary"),
      })
      .default({})
      .describe("Event details"),
  },
  async ({ event, details }) => {
    const message = formatProgressMessage(event, details);

    // Fire-and-forget to daemon — don't fail if daemon is down
    try {
      await queryDaemonWithRetry({ action: "progress", event, details }, 3000, 1);
    } catch {
      // Progress tracking is best-effort
    }

    return {
      content: [{ type: "text" as const, text: message }],
    };
  }
);

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
