export interface WorkItemLite {
  id: number;
  titre?: string;
  statut?: string;
  priorite?: number | string;
  assigne_a?: string;
  type?: string;
}

export interface FunctionRef {
  id?: string;
  name?: string;
}

export interface RetestItem {
  type: "CODE-RETEST" | "FILE-RETEST" | "INFRA-RETEST" | string;
  element: string;
  why?: string;
  description?: string;
  what_to_test?: string;
  evidence?: string;
}

export interface TargetRetest {
  microservice: string;
  commit_count: number;
  files_count: number;
  target_functions?: FunctionRef[];
  retest_items?: RetestItem[];
}

export interface ScopeSummary {
  direct_ms_count: number;
  indirect_ms_count: number;
  total_ms_count: number;
  total_functions: number;
  commit_count?: number;
  commit_trace_functions?: number;
  commit_trace_infra_blocks?: number;
  heuristics_enabled?: boolean;
  fallback_source?: string | null;
}

export interface NrtScopeResponse {
  status?: string;
  error?: string;
  workitem?: {
    id: number;
    title: string;
    type: string;
  };
  summary?: ScopeSummary;
  direct_microservices?: Array<{ name: string; functions?: FunctionRef[] }>;
  indirect_microservices?: Array<{ name: string; functions?: FunctionRef[] }>;
  target_retest_from_commit_trace?: TargetRetest[];
}

