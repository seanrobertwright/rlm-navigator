/**
 * Unit tests for MCP server utility functions.
 */

import { describe, test, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import {
  truncateResponse,
  formatSize,
  formatTree,
  readLines,
  getDaemonPort,
  formatProgressMessage,
} from "../src/utils.js";

// ---------------------------------------------------------------------------
// truncateResponse
// ---------------------------------------------------------------------------

describe("truncateResponse", () => {
  test("returns short text unchanged", () => {
    const text = "Hello world";
    expect(truncateResponse(text, 100)).toBe(text);
  });

  test("truncates text exceeding maxChars", () => {
    const text = "a".repeat(200);
    const result = truncateResponse(text, 100);
    expect(result.length).toBeLessThan(text.length);
    expect(result).toContain("truncated");
    expect(result).toContain("100 more chars");
  });

  test("includes token estimate in truncation message", () => {
    const text = "x".repeat(500);
    const result = truncateResponse(text, 100);
    // 400 remaining chars / 4 = ~100 tokens
    expect(result).toContain("~100 tokens");
  });

  test("returns exact maxChars text unchanged", () => {
    const text = "a".repeat(100);
    expect(truncateResponse(text, 100)).toBe(text);
  });
});

// ---------------------------------------------------------------------------
// formatSize
// ---------------------------------------------------------------------------

describe("formatSize", () => {
  test("formats bytes", () => {
    expect(formatSize(500)).toBe("500B");
  });

  test("formats kilobytes", () => {
    expect(formatSize(2048)).toBe("2.0KB");
  });

  test("formats megabytes", () => {
    expect(formatSize(1048576)).toBe("1.0MB");
  });

  test("handles zero", () => {
    expect(formatSize(0)).toBe("0B");
  });
});

// ---------------------------------------------------------------------------
// formatTree
// ---------------------------------------------------------------------------

describe("formatTree", () => {
  test("formats flat file list", () => {
    const entries = [
      { type: "file", name: "main.py", path: "main.py", size: 1024, language: "python" },
      { type: "file", name: "README.md", path: "README.md", size: 256, language: null },
    ];
    const result = formatTree(entries, "");
    expect(result).toContain("main.py");
    expect(result).toContain("[python]");
    expect(result).toContain("README.md");
  });

  test("formats directories with children count", () => {
    const entries = [
      {
        type: "dir",
        name: "src",
        path: "src",
        children: 3,
      },
    ];
    const result = formatTree(entries, "");
    expect(result).toContain("src/");
    expect(result).toContain("3 items");
  });

  test("formats nested directory with entries", () => {
    const entries = [
      {
        type: "dir",
        name: "src",
        path: "src",
        entries: [
          { type: "file", name: "app.py", path: "src/app.py", size: 512, language: "python" },
        ],
      },
    ];
    const result = formatTree(entries, "");
    expect(result).toContain("src/");
    expect(result).toContain("  app.py");
  });

  test("handles indent parameter", () => {
    const entries = [
      { type: "file", name: "test.js", path: "test.js", size: 100, language: "javascript" },
    ];
    const result = formatTree(entries, "    ");
    expect(result.startsWith("    ")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// readLines
// ---------------------------------------------------------------------------

describe("readLines", () => {
  test("reads specific line range with line numbers", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-test-"));
    const filePath = path.join(tmpDir, "test.py");
    fs.writeFileSync(filePath, "line1\nline2\nline3\nline4\nline5\n");

    const result = readLines(filePath, 2, 4);
    expect(result).toContain("line2");
    expect(result).toContain("line3");
    expect(result).toContain("line4");
    expect(result).not.toContain("line1");
    expect(result).not.toContain("line5");

    fs.rmSync(tmpDir, { recursive: true });
  });

  test("includes padded line numbers", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-test-"));
    const filePath = path.join(tmpDir, "numbered.py");
    fs.writeFileSync(filePath, "a\nb\nc\n");

    const result = readLines(filePath, 1, 3);
    expect(result).toMatch(/\d+ \| a/);
    expect(result).toMatch(/\d+ \| b/);
    expect(result).toMatch(/\d+ \| c/);

    fs.rmSync(tmpDir, { recursive: true });
  });
});

// ---------------------------------------------------------------------------
// getDaemonPort
// ---------------------------------------------------------------------------

describe("getDaemonPort", () => {
  test("reads port from JSON port file", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-test-"));
    const rlmDir = path.join(tmpDir, ".rlm");
    fs.mkdirSync(rlmDir);
    // Use current PID so isPidAlive returns true
    fs.writeFileSync(
      path.join(rlmDir, "port"),
      JSON.stringify({ port: 9200, pid: process.pid })
    );

    const port = getDaemonPort(tmpDir);
    expect(port).toBe(9200);

    fs.rmSync(tmpDir, { recursive: true });
  });

  test("returns null when port file missing and .rlm exists", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-test-"));
    const rlmDir = path.join(tmpDir, ".rlm");
    fs.mkdirSync(rlmDir);

    const port = getDaemonPort(tmpDir);
    expect(port).toBeNull();

    fs.rmSync(tmpDir, { recursive: true });
  });

  test("cleans up stale port file with dead PID", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-test-"));
    const rlmDir = path.join(tmpDir, ".rlm");
    fs.mkdirSync(rlmDir);
    const portFile = path.join(rlmDir, "port");
    fs.writeFileSync(portFile, JSON.stringify({ port: 9200, pid: 999999999 }));

    const port = getDaemonPort(tmpDir);
    expect(port).toBeNull();
    expect(fs.existsSync(portFile)).toBe(false);

    fs.rmSync(tmpDir, { recursive: true });
  });

  test("returns default 9177 when no .rlm directory exists", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-test-"));

    const port = getDaemonPort(tmpDir);
    expect(port).toBe(9177);

    fs.rmSync(tmpDir, { recursive: true });
  });

  test("respects env var override", () => {
    const port = getDaemonPort("/dummy", "19500");
    expect(port).toBe(19500);
  });
});

// ---------------------------------------------------------------------------
// formatProgressMessage
// ---------------------------------------------------------------------------

describe("formatProgressMessage", () => {
  test("chunking_start with query", () => {
    const msg = formatProgressMessage("chunking_start", { file: "main.py", query: "auth flow" });
    expect(msg).toBe("[RLM] Chunking main.py for: auth flow...");
  });

  test("chunking_start without query", () => {
    const msg = formatProgressMessage("chunking_start", { file: "main.py" });
    expect(msg).toBe("[RLM] Chunking main.py...");
  });

  test("chunking_complete", () => {
    const msg = formatProgressMessage("chunking_complete", { file: "main.py", total_chunks: 7 });
    expect(msg).toBe("[RLM] main.py split into 7 chunks");
  });

  test("chunk_dispatch with chunk info", () => {
    const msg = formatProgressMessage("chunk_dispatch", {
      file: "main.py", agent: "rlm-subcall", chunk: 2, total_chunks: 7
    });
    expect(msg).toBe("[RLM] Dispatching chunk 3/7 of main.py to rlm-subcall...");
  });

  test("chunk_dispatch for enrichment", () => {
    const msg = formatProgressMessage("chunk_dispatch", {
      file: "main.py", agent: "rlm-enricher"
    });
    expect(msg).toBe("[RLM] Dispatching main.py to rlm-enricher...");
  });

  test("chunk_complete with count", () => {
    const msg = formatProgressMessage("chunk_complete", {
      chunk: 2, total_chunks: 7, count: 3
    });
    expect(msg).toBe("[RLM] chunk 3/7 complete — 3 relevant symbols found");
  });

  test("chunk_complete with summary", () => {
    const msg = formatProgressMessage("chunk_complete", {
      chunk: 0, total_chunks: 1, count: 2, summary: "found auth handler"
    });
    expect(msg).toBe("[RLM] chunk 1/1 complete — 2 relevant symbols found — found auth handler");
  });

  test("queries_suggested", () => {
    const msg = formatProgressMessage("queries_suggested", { count: 5 });
    expect(msg).toBe("[RLM] 5 follow-up queries suggested");
  });

  test("answer_found with summary", () => {
    const msg = formatProgressMessage("answer_found", { summary: "JWT validation in middleware" });
    expect(msg).toBe("[RLM] Answer found: JWT validation in middleware");
  });

  test("answer_found without summary", () => {
    const msg = formatProgressMessage("answer_found", {});
    expect(msg).toBe("[RLM] Answer found");
  });

  test("synthesis_start", () => {
    const msg = formatProgressMessage("synthesis_start", { count: 12 });
    expect(msg).toBe("[RLM] Synthesis starting — analyzing 12 findings");
  });

  test("synthesis_complete", () => {
    const msg = formatProgressMessage("synthesis_complete", { summary: "3 key patterns identified" });
    expect(msg).toBe("[RLM] Synthesis complete: 3 key patterns identified");
  });

  test("unknown event fallback", () => {
    const msg = formatProgressMessage("custom_event", {});
    expect(msg).toBe("[RLM] custom_event");
  });
});
