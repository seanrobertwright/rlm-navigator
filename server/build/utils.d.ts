/**
 * Shared utility functions for RLM Navigator MCP server.
 *
 * Extracted for testability — these are pure or filesystem-only functions
 * with no MCP server dependencies.
 */
export declare function truncateResponse(text: string, maxChars?: number): string;
export declare function formatSize(bytes: number): string;
export declare function formatTree(entries: any[], indent: string): string;
export declare function formatStats(result: any): string;
export declare function formatStalenessWarning(staleness: any): string;
export declare function readLines(filePath: string, startLine: number, endLine: number): string;
export declare function isPidAlive(pid: number): boolean;
export declare function getDaemonPort(projectRoot: string, envPort?: string): number | null;
export declare function queryDaemon(request: object, timeoutMs?: number, port?: number): Promise<any>;
//# sourceMappingURL=utils.d.ts.map