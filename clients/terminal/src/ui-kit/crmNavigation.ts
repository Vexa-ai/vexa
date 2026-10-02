/** CRM links have a native route fallback; Minutes claims them for its right panel. */
export const OPEN_CRM_RECORD = "vexa:open-crm-record";
export function openCrmRecord(href: string): boolean {
  const url = new URL(href, window.location.origin);
  if (url.origin !== window.location.origin || url.pathname !== "/crm") return false;
  const recordId = url.searchParams.get("record");
  if (!recordId && !url.searchParams.get("object") && !url.searchParams.get("name")) return false;
  return !window.dispatchEvent(new CustomEvent(OPEN_CRM_RECORD, { cancelable: true, detail: { recordId, href: url.pathname + url.search } }));
}
