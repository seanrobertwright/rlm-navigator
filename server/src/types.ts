/**
 * TypeScript interfaces for daemon TCP responses.
 *
 * These match the JSON shapes returned by the Python daemon's handle_request().
 */

// ---------------------------------------------------------------------------
// Session stats (injected into every successful response)
// ---------------------------------------------------------------------------

export interface SessionStatsSnapshot {
  tokens_served: number;
  tokens_avoided: number;
  reduction_pct: number;
  tool_calls: number;
}

export interface DaemonBaseResponse {
  _stats?: SessionStatsSnapshot;
  error?: string;
}

// ---------------------------------------------------------------------------
// Action-specific responses
// ---------------------------------------------------------------------------

export interface DaemonStatusResponse extends DaemonBaseResponse {
  status: "alive";
  root: string;
  cache_size: number;
  languages: string[];
  enrichment_available: boolean;
  doc_indexing_available: boolean;
  session?: {
    tool_calls: number;
    tokens_served: number;
    tokens_avoided: number;
    reduction_pct: number;
    duration_s: number;
    breakdown: Record<string, { calls: number; tokens_served: number; tokens_avoided?: number }>;
    progress_events: Array<{ event: string; details: Record<string, unknown>; timestamp: number }>;
    progress_summary: ProgressSummary;
    progress_last_event?: { event: string; details: Record<string, unknown>; timestamp: number };
  };
}

export interface ProgressSummary {
  sub_agent_dispatches: number;
  chunks_analyzed: number;
  answers_found: number;
  enrichments: number;
  analyses: number;
}

export interface DaemonSqueezeResponse extends DaemonBaseResponse {
  skeleton: string;
}

export interface DaemonFindResponse extends DaemonBaseResponse {
  start_line: number;
  end_line: number;
}

export interface TreeEntry {
  type: "file" | "dir";
  name: string;
  path: string;
  size?: number;
  language?: string | null;
  children?: number;
  entries?: TreeEntry[];
}

export interface DaemonTreeResponse extends DaemonBaseResponse {
  tree: TreeEntry[];
}

export interface SearchMatch {
  path: string;
  matches: string[];
}

export interface DaemonSearchResponse extends DaemonBaseResponse {
  results: SearchMatch[];
}

export interface DaemonDocMapResponse extends DaemonBaseResponse {
  tree: {
    name: string;
    type: string;
    children: Array<{
      name: string;
      type: string;
      range?: { start: number; end: number };
      children?: unknown[];
    }>;
  };
}

export interface DaemonDocDrillResponse extends DaemonBaseResponse {
  content: string;
}

export interface DaemonAssessResponse extends DaemonBaseResponse {
  assessment: string;
}

export interface ChunkManifest {
  total_chunks: number;
  chunk_size: number;
  overlap: number;
  total_lines: number;
  mtime: number;
}

export interface DaemonChunksListResponse extends DaemonBaseResponse {
  status: "ready" | "pending";
  manifest?: ChunkManifest;
}

export interface DaemonChunksReadResponse extends DaemonBaseResponse {
  content: string;
  chunk: number;
  total_chunks: number;
  lines: string;
}

export interface DaemonProgressResponse extends DaemonBaseResponse {
  ok: boolean;
}

export interface DaemonErrorResponse {
  error: string;
  _stats?: SessionStatsSnapshot;
}

// ---------------------------------------------------------------------------
// REPL responses
// ---------------------------------------------------------------------------

export interface DaemonReplExecResponse extends DaemonBaseResponse {
  output?: string;
  error?: string;
  hint?: string;
  variables?: string[];
  timed_out?: boolean;
  staleness_warning?: StalenessWarning;
}

export interface DaemonReplStatusResponse extends DaemonBaseResponse {
  variables: string[];
  buffer_count: Record<string, number>;
  exec_count: number;
  staleness?: StalenessWarning;
}

export interface DaemonReplExportResponse extends DaemonBaseResponse {
  buffers: Record<string, string[]>;
}

export interface StalenessWarning {
  variables?: Record<string, Array<{ file: string; reason: string }>>;
  buffers?: Record<string, Array<{ file: string; reason: string }>>;
}

// ---------------------------------------------------------------------------
// Progress details
// ---------------------------------------------------------------------------

export interface ProgressDetails {
  file?: string;
  agent?: string;
  chunk?: number;
  total_chunks?: number;
  query?: string;
  count?: number;
  summary?: string;
}
