import { fetchNrtScopeByWorkItem, fetchWorkItemsByType } from "../client/workitem-scope-api";
import type { NrtScopeResponse, WorkItemLite } from "../types/workitem-scope";

type BridgeApi = {
  fetchWorkItemsByType: (type: string) => Promise<WorkItemLite[]>;
  fetchNrtScopeByWorkItem: (workItemId: number) => Promise<NrtScopeResponse>;
};

declare global {
  interface Window {
    NRT_TS_API?: BridgeApi;
  }
}

window.NRT_TS_API = {
  fetchWorkItemsByType,
  fetchNrtScopeByWorkItem,
};

