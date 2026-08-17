const STORAGE_KEY = "vexa-pending-meeting-url";

export function savePendingMeetingUrl(url: string) {
  if (typeof window !== "undefined") {
    sessionStorage.setItem(STORAGE_KEY, url);
  }
}

export function consumePendingMeetingUrl(): string | null {
  if (typeof window === "undefined") return null;
  const url = sessionStorage.getItem(STORAGE_KEY);
  if (url) {
    sessionStorage.removeItem(STORAGE_KEY);
  }
  return url;
}
