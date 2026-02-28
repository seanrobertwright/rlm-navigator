/**
 * Shared utility functions for RLM Navigator MCP server.
 *
 * Extracted for testability — these are pure or filesystem-only functions
 * with no MCP server dependencies.
 */

import * as net from "node:net";
import * as fs from "node:fs";
import * as path from "node:path";
import type { ProgressDetails } from "./types.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const DAEMON_PORT_RANGE = { start: 9177, end: 9196 } as const;

// ---------------------------------------------------------------------------
// MCP tool response helpers
// ---------------------------------------------------------------------------

interface McpTextContent {
  type: "text";
  text: string;
  [key: string]: unknown;
}

interface McpToolResponse {
  content: McpTextContent[];
  isError?: boolean;
  [key: string]: unknown;
}

export function toolError(message: string): McpToolResponse {
  return {
    content: [{ type: "text" as const, text: message }],
    isError: true,
  };
}

export function toolSuccess(text: string): McpToolResponse {
  return {
    content: [{ type: "text" as const, text }],
  };
}

export function handleToolError(err: unknown): McpToolResponse {
  const message = err instanceof Error ? err.message : String(err);
  return toolError(`Daemon error: ${message}. Is the daemon running?`);
}

export function checkDaemonResponse(result: Record<string, unknown>): McpToolResponse | null {
  if (result.error) {
    return toolError(`Error: ${result.error}`);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Output truncation
// ---------------------------------------------------------------------------

export function truncateResponse(text: string, maxChars: number = 8000): string {
  if (text.length <= maxChars) return text;
  const remaining = text.length - maxChars;
  const tokensEst = Math.round(remaining / 4);
  return text.slice(0, maxChars) + `\n... (truncated, ${remaining} more chars, ~${tokensEst} tokens)`;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export function formatTree(entries: any[], indent: string): string {
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

export function formatStats(result: any): string {
  const s = result?._stats;
  if (!s) return "";
  return `\n\n📊 Session: ${s.tokens_served.toLocaleString()} tokens served | ${s.tokens_avoided.toLocaleString()} avoided (${s.reduction_pct}% reduction) | ${s.tool_calls} calls`;
}

export function formatStalenessWarning(staleness: any): string {
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

// ---------------------------------------------------------------------------
// File reading
// ---------------------------------------------------------------------------

export function readLines(
  filePath: string,
  startLine: number,
  endLine: number
): string {
  const content = fs.readFileSync(filePath, "utf-8");
  const lines = content.split("\n");
  const selected = lines.slice(startLine - 1, endLine);
  return selected
    .map((line, i) => `${(startLine + i).toString().padStart(4)} | ${line}`)
    .join("\n");
}

// ---------------------------------------------------------------------------
// PID helpers
// ---------------------------------------------------------------------------

export function formatProgressMessage(event: string, details: ProgressDetails): string {
  const file = details.file || "unknown";
  const agent = details.agent || "sub-agent";
  const chunk = details.chunk;
  const total = details.total_chunks;
  const count = details.count;
  const summary = details.summary;
  const query = details.query;

  const chunkLabel = chunk !== undefined && total !== undefined
    ? `chunk ${chunk + 1}/${total}` : "";

  switch (event) {
    case "chunking_start":
      return `[RLM] Chunking ${file}${query ? ` for: ${query}` : ""}...`;
    case "chunking_complete":
      return `[RLM] ${file} split into ${total} chunks`;
    case "chunk_dispatch":
      return `[RLM] Dispatching ${chunkLabel ? chunkLabel + " of " : ""}${file} to ${agent}...`;
    case "chunk_complete": {
      const parts = [`[RLM] ${chunkLabel ? chunkLabel + " " : ""}complete`];
      if (count !== undefined) parts.push(`${count} relevant symbols found`);
      if (summary) parts.push(summary);
      return parts.join(" — ");
    }
    case "queries_suggested":
      return `[RLM] ${count || 0} follow-up queries suggested`;
    case "answer_found":
      return `[RLM] Answer found${summary ? ": " + summary : ""}`;
    case "synthesis_start":
      return `[RLM] Synthesis starting — analyzing ${count || "?"} findings`;
    case "synthesis_complete":
      return `[RLM] Synthesis complete${summary ? ": " + summary : ""}`;
    default:
      return `[RLM] ${event}`;
  }
}

// ---------------------------------------------------------------------------
// PID helpers
// ---------------------------------------------------------------------------

export function isConnectionError(err: unknown): err is NodeJS.ErrnoException {
  if (!(err instanceof Error)) return false;
  const code = (err as NodeJS.ErrnoException).code;
  return code === "ECONNREFUSED" || code === "ECONNRESET" || code === "EPIPE";
}

export function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Port discovery
// ---------------------------------------------------------------------------

export interface PortFileData {
  port: number;
  pid: number | null;
}

export function readPortFile(projectRoot: string): PortFileData | null {
  const portFile = path.join(projectRoot, ".rlm", "port");
  try {
    const raw = fs.readFileSync(portFile, "utf-8").trim();

    let port: number;
    let pid: number | null = null;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === "object" && parsed !== null) {
        port = parsed.port;
        pid = parsed.pid || null;
      } else {
        // JSON.parse("9185") yields a number — treat as plain port
        port = Number(parsed);
      }
    } catch {
      port = parseInt(raw, 10);
    }

    if (isNaN(port)) return null;

    // If we have a PID, check if it's still alive
    if (pid !== null && !isPidAlive(pid)) {
      try { fs.unlinkSync(portFile); } catch {}
      return null;
    }

    return { port, pid };
  } catch {
    return null;
  }
}

export function getDaemonPort(projectRoot: string, envPort?: string): number | null {
  // 1. Env var override
  if (envPort) {
    return parseInt(envPort, 10);
  }
  // 2. Read .rlm/port file
  const portData = readPortFile(projectRoot);
  if (portData) return portData.port;
  // 3. If .rlm/ exists, don't fall back (would hit wrong daemon)
  const rlmDir = path.join(projectRoot, ".rlm");
  if (fs.existsSync(rlmDir)) return null;
  // 4. Legacy mode — default port
  return 9177;
}

// ---------------------------------------------------------------------------
// TCP client
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export function queryDaemon<T = Record<string, unknown>>(request: object, timeoutMs = 10000, port?: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const targetPort = port ?? null;
    if (targetPort === null) {
      reject(Object.assign(new Error("No daemon port specified"), { code: "ECONNREFUSED" }));
      return;
    }
    const client = new net.Socket();
    let data = Buffer.alloc(0);
    const timer = setTimeout(() => {
      client.destroy();
      reject(new Error("Daemon query timed out"));
    }, timeoutMs);

    client.connect(targetPort, "127.0.0.1", () => {
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
        resolve({ raw: data.toString("utf-8") } as unknown as T);
      }
    });

    client.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}
