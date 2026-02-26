MCP Server

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { execSync } from "child_process";
import net from "net";

const server = new McpServer({ name: "rlm-navigator", version: "1.0.0" });

server.tool("get_status", "Check RLM daemon health", {}, async () => {
    return new Promise((resolve) => {
        const client = net.createConnection({ path: "/tmp/rlm_daemon.sock" });
        client.on('connect', () => { client.end(); resolve({ content: [{ type: "text", text: "ONLINE" }] }); });
        client.on('error', () => resolve({ content: [{ type: "text", text: "OFFLINE" }] }));
    });
});

server.tool("rlm_map", "Get structural skeleton", { path: z.string() }, async ({ path }) => {
    const out = execSync(`python3 ../daemon/Maps.py --file ${path} --mode squeeze`).toString();
    return { content: [{ type: "text", text: out }] };
});

server.tool("rlm_drill", "Surgical read", { path: z.string(), symbol: z.string() }, async ({ path, symbol }) => {
    const range = execSync(`python3 ../daemon/Maps.py --file ${path} --mode find --symbol ${symbol}`).toString().trim();
    const [start, end] = range.split('-');
    const code = execSync(`sed -n '${start},${end}p' ${path}`).toString();
    return { content: [{ type: "text", text: code }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
```

```md
name: recursive_navigator
description: Enforces a recursive, token-efficient RLM workflow for large codebases.

Recursive Navigator Skill
1. Check Health: Always run get_status first.
2. Map Structure: Use rlm_map to see signatures only. NEVER cat files > 100 lines.
3. Drill Down: Use rlm_drill to fetch implementation only when logic is identified as relevant.
4. Context Rule: Clear context between unrelated module deep-dives.
```

Install.sh

```bash

```


