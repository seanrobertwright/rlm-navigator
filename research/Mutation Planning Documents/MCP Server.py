import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { execSync } from "child_process";
import net from "net";

const server = new McpServer({ 
    name: "rlm-navigator-reasoning", 
    version: "2.0.0" 
});

/**
 * Health Check Tool: Standard RLM Handshake
 */
server.tool("get_status", "Checks if the live RLM daemon is watching the codebase.", {}, async () => {
    return new Promise((resolve) => {
        const client = net.createConnection({ path: "/tmp/rlm_daemon.sock" });
        client.on('connect', () => { 
            client.end(); 
            resolve({ content: [{ type: "text", text: "DAEMON_ONLINE: Live Updates Enabled." }] }); 
        });
        client.on('error', () => resolve({ content: [{ type: "text", text: "DAEMON_OFFLINE: Warning, index may be stale." }] }));
    });
});

/**
 * Reasoning Tool: PageIndex Mutation
 * Provides a reasoning-compatible tree of symbols for a specific file/module.
 */
server.tool(
    "rlm_reason_tree", 
    "Returns a hierarchical tree of symbols with docstrings for reasoning-based navigation.", 
    { path: z.string() }, 
    async ({ path }) => {
        const out = execSync(`python3 ../daemon/Maps.py --file ${path} --mode tree`).toString();
        return { content: [{ type: "text", text: out }] };
    }
);

/**
 * Surgical Fetch: The "Drill Down" Tool
 */
server.tool(
    "rlm_surgical_fetch", 
    "Extracts exact implementation logic for a specific line range.", 
    { 
        path: z.string(), 
        range: z.string().describe("e.g., '20-45'") 
    }, 
    async ({ path, range }) => {
        const [start, end] = range.split('-');
        const code = execSync(`sed -n '${start},${end}p' ${path}`).toString();
        return { content: [{ type: "text", text: code }] };
    }
);

const transport = new StdioServerTransport();
await server.connect(transport);