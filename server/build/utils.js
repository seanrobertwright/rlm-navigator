/**
 * Shared utility functions for RLM Navigator MCP server.
 *
 * Extracted for testability — these are pure or filesystem-only functions
 * with no MCP server dependencies.
 */
import * as net from "node:net";
import * as fs from "node:fs";
import * as path from "node:path";
// ---------------------------------------------------------------------------
// Output truncation
// ---------------------------------------------------------------------------
export function truncateResponse(text, maxChars = 8000) {
    if (text.length <= maxChars)
        return text;
    const remaining = text.length - maxChars;
    const tokensEst = Math.round(remaining / 4);
    return text.slice(0, maxChars) + `\n... (truncated, ${remaining} more chars, ~${tokensEst} tokens)`;
}
// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
export function formatSize(bytes) {
    if (bytes < 1024)
        return `${bytes}B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
export function formatTree(entries, indent) {
    const lines = [];
    for (const entry of entries) {
        if (entry.type === "dir") {
            const childCount = typeof entry.children === "number"
                ? entry.children
                : entry.entries?.length ?? 0;
            lines.push(`${indent}${entry.name}/ (${childCount} items)`);
            if (entry.entries) {
                lines.push(formatTree(entry.entries, indent + "  "));
            }
        }
        else {
            const size = formatSize(entry.size || 0);
            const lang = entry.language ? ` [${entry.language}]` : "";
            lines.push(`${indent}${entry.name} (${size})${lang}`);
        }
    }
    return lines.join("\n");
}
export function formatStats(result) {
    const s = result?._stats;
    if (!s)
        return "";
    return `\n\n📊 Session: ${s.tokens_served.toLocaleString()} tokens served | ${s.tokens_avoided.toLocaleString()} avoided (${s.reduction_pct}% reduction) | ${s.tool_calls} calls`;
}
export function formatStalenessWarning(staleness) {
    const lines = ["\n⚠ STALE DATA WARNING:"];
    if (staleness.variables) {
        for (const [varName, files] of Object.entries(staleness.variables)) {
            const fileList = files.map((f) => `${f.file} (${f.reason})`).join(", ");
            lines.push(`  var '${varName}': ${fileList}`);
        }
    }
    if (staleness.buffers) {
        for (const [bufName, files] of Object.entries(staleness.buffers)) {
            const fileList = files.map((f) => `${f.file} (${f.reason})`).join(", ");
            lines.push(`  buffer '${bufName}': ${fileList}`);
        }
    }
    return lines.join("\n");
}
// ---------------------------------------------------------------------------
// File reading
// ---------------------------------------------------------------------------
export function readLines(filePath, startLine, endLine) {
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
export function isPidAlive(pid) {
    try {
        process.kill(pid, 0);
        return true;
    }
    catch {
        return false;
    }
}
// ---------------------------------------------------------------------------
// Port discovery
// ---------------------------------------------------------------------------
export function getDaemonPort(projectRoot, envPort) {
    // 1. Env var override
    if (envPort) {
        return parseInt(envPort, 10);
    }
    // 2. Read .rlm/port file
    try {
        const portFile = path.join(projectRoot, ".rlm", "port");
        const raw = fs.readFileSync(portFile, "utf-8").trim();
        let port;
        let pid = null;
        try {
            const parsed = JSON.parse(raw);
            port = parsed.port;
            pid = parsed.pid || null;
        }
        catch {
            port = parseInt(raw, 10);
        }
        if (isNaN(port))
            return null;
        // If we have a PID, check if it's still alive
        if (pid !== null && !isPidAlive(pid)) {
            try {
                fs.unlinkSync(portFile);
            }
            catch { }
            return null;
        }
        return port;
    }
    catch {
        // File doesn't exist
    }
    // 3. If .rlm/ exists, don't fall back (would hit wrong daemon)
    const rlmDir = path.join(projectRoot, ".rlm");
    if (fs.existsSync(rlmDir))
        return null;
    // 4. Legacy mode — default port
    return 9177;
}
// ---------------------------------------------------------------------------
// TCP client
// ---------------------------------------------------------------------------
function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}
export function queryDaemon(request, timeoutMs = 10000, port) {
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
            }
            catch {
                resolve({ raw: data.toString("utf-8") });
            }
        });
        client.on("error", (err) => {
            clearTimeout(timer);
            reject(err);
        });
    });
}
//# sourceMappingURL=utils.js.map