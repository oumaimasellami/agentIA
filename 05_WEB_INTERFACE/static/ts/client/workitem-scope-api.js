async function getJson(url) {
    const response = await fetch(url, { method: "GET" });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status} on ${url}`);
    }
    return (await response.json());
}
export async function fetchWorkItemsByType(type) {
    const encoded = encodeURIComponent(type);
    return getJson(`/api/workitems-by-type/${encoded}`);
}
export async function fetchNrtScopeByWorkItem(workItemId) {
    return getJson(`/api/nrt-scope?workitem_id=${workItemId}`);
}
export function asShortCommit(commitId) {
    return String(commitId || "").slice(0, 8);
}
