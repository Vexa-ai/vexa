export interface ProfileApiErrorBody {
  error?: unknown;
  detail?: unknown;
  message?: unknown;
}

function renderDetail(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const record = item as Record<string, unknown>;
        const location = Array.isArray(record.loc)
          ? record.loc
              .filter(
                (part): part is string | number =>
                  typeof part === "string" || typeof part === "number"
              )
              .join(".")
          : "";
        const message =
          typeof record.msg === "string"
            ? record.msg
            : typeof record.message === "string"
              ? record.message
              : "";
        return [location, message].filter(Boolean).join(": ") || null;
      })
      .filter((message): message is string => Boolean(message));
    return messages.length > 0 ? messages.join("; ") : null;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    for (const key of ["message", "detail", "error"]) {
      const nested = renderDetail(record[key]);
      if (nested) return nested;
    }
  }
  return null;
}

export async function profileApiErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as ProfileApiErrorBody;
    return (
      renderDetail(body.error) ??
      renderDetail(body.detail) ??
      renderDetail(body.message) ??
      `Account request failed (HTTP ${response.status}).`
    );
  } catch {
    return `Account request failed (HTTP ${response.status}).`;
  }
}

export function tokenMetadataPreview(scopes: string[]): string {
  const primary = scopes.find((scope) =>
    ["bot", "tx", "browser"].includes(scope)
  );
  return primary ? `vxa_${primary}_••••` : "Secret shown only once";
}
