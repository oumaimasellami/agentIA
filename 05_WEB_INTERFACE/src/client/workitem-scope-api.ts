import type { NrtScopeResponse, WorkItemLite } from "../types/workitem-scope";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { method: "GET" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} on ${url}`);
  }
  return (await response.json()) as T;
}

export async function fetchWorkItemsByType(type: string): Promise<WorkItemLite[]> {
  const encoded = encodeURIComponent(type);
  return getJson<WorkItemLite[]>(`/api/workitems-by-type/${encoded}`);
}

export async function fetchNrtScopeByWorkItem(workItemId: number): Promise<NrtScopeResponse> {
  return getJson<NrtScopeResponse>(`/api/nrt-scope?workitem_id=${workItemId}`);
}

export function asShortCommit(commitId: string): string {
  return String(commitId || "").slice(0, 8);
}

