/**
 * E2E tests for MCP server ↔ daemon communication.
 *
 * These tests start a real Python daemon and verify the TypeScript
 * TCP client can communicate with it.
 */

import { describe, test, expect, beforeAll, afterAll } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import * as net from "node:net";
import { spawn, ChildProcess } from "node:child_process";
import { queryDaemon } from "../src/utils.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getFreeTcpPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      if (addr && typeof addr === "object") {
        const port = addr.port;
        srv.close(() => resolve(port));
      } else {
        reject(new Error("Could not get port"));
      }
    });
    srv.on("error", reject);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function startDaemon(
  root: string,
  port: number
): Promise<ChildProcess> {
  const daemonScript = path.resolve(
    __dirname,
    "..",
    "..",
    "daemon",
    "rlm_daemon.py"
  );

  const child = spawn("python", [daemonScript, "--root", root, "--port", String(port), "--idle-timeout", "0"], {
    stdio: "pipe",
  });

  // Wait for port file
  const portFile = path.join(root, ".rlm", "port");
  for (let i = 0; i < 50; i++) {
    await sleep(100);
    if (fs.existsSync(portFile)) return child;
  }

  child.kill();
  throw new Error("Daemon failed to start");
}

function tcpQuery(port: number, request: object, timeout = 5000): Promise<any> {
  return new Promise((resolve, reject) => {
    const client = new net.Socket();
    let data = Buffer.alloc(0);
    const timer = setTimeout(() => {
      client.destroy();
      reject(new Error("TCP query timed out"));
    }, timeout);

    client.connect(port, "127.0.0.1", () => {
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

// ---------------------------------------------------------------------------
// E2E tests
// ---------------------------------------------------------------------------

describe("MCP ↔ Daemon E2E", () => {
  let tmpDir: string;
  let port: number;
  let daemon: ChildProcess;

  beforeAll(async () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rlm-e2e-"));
    const rlmDir = path.join(tmpDir, ".rlm");
    fs.mkdirSync(rlmDir);

    // Create test source files
    fs.writeFileSync(
      path.join(tmpDir, "app.py"),
      "class App:\n    def run(self):\n        pass\n"
    );
    fs.writeFileSync(
      path.join(tmpDir, "README.md"),
      "# Test Project\n\n## Setup\n\nRun pip install.\n"
    );

    port = await getFreeTcpPort();
    daemon = await startDaemon(tmpDir, port);
  }, 15000);

  afterAll(async () => {
    if (daemon) {
      try {
        await tcpQuery(port, { action: "shutdown" }, 2000);
      } catch {
        // ignore
      }
      await sleep(500);
      daemon.kill();
    }
    if (tmpDir) {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test("daemon responds to status query via TCP", async () => {
    const result = await tcpQuery(port, { action: "status" });
    expect(result.status).toBe("alive");
    expect(result.root).toBeTruthy();
  });

  test("queryDaemon utility connects to daemon", async () => {
    const result = await queryDaemon({ action: "status" }, 5000, port);
    expect(result.status).toBe("alive");
  });

  test("squeeze returns skeleton for Python file", async () => {
    const result = await tcpQuery(port, {
      action: "squeeze",
      path: "app.py",
    });
    expect(result.skeleton).toBeTruthy();
    expect(result.skeleton).toContain("App");
  });

  test("find returns line range for symbol", async () => {
    const result = await tcpQuery(port, {
      action: "find",
      path: "app.py",
      symbol: "App",
    });
    expect(result.start_line).toBeDefined();
    expect(result.end_line).toBeDefined();
    expect(result.start_line).toBeLessThanOrEqual(result.end_line);
  });

  test("tree returns directory structure", async () => {
    const result = await tcpQuery(port, { action: "tree" });
    expect(result.tree).toBeDefined();
    expect(Array.isArray(result.tree)).toBe(true);
    const names = result.tree.map((e: any) => e.name);
    expect(names).toContain("app.py");
  });

  test("search finds symbol across files", async () => {
    const result = await tcpQuery(port, {
      action: "search",
      query: "App",
    });
    expect(result.results).toBeDefined();
    expect(result.results.length).toBeGreaterThanOrEqual(1);
  });

  test("doc_map returns document tree for markdown", async () => {
    const result = await tcpQuery(port, {
      action: "doc_map",
      path: "README.md",
    });
    expect(result.tree).toBeDefined();
    expect(result.tree.type).toBe("document");
  });

  test("unknown action returns error", async () => {
    const result = await tcpQuery(port, { action: "nonexistent" });
    expect(result.error).toBeDefined();
    expect(result.error).toContain("Unknown action");
  });

  test("invalid path returns error", async () => {
    const result = await tcpQuery(port, {
      action: "squeeze",
      path: "does_not_exist.py",
    });
    expect(result.error).toBeDefined();
  });
});
