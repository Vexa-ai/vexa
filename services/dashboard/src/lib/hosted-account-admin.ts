/**
 * Hosted Account adapter for the stock v0.12 Admin API.
 *
 * The server-side dashboard owns this translation boundary. Browser identity
 * fields never cross it, and token secrets cross only on a successful mint.
 */

const VALID_SCOPES = new Set(["bot", "tx", "browser"]);

export interface HostedAdminConfig {
  baseUrl: string;
  adminKey: string;
  fetcher?: typeof fetch;
}

export interface HostedTokenMetadata {
  id: string;
  scopes: string[];
  name: string | null;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface HostedMintedToken {
  id: number;
  token: string;
  user_id: number;
  scopes: string[];
}

export interface HostedMintInput {
  scopes: string[];
  name?: string;
  expires_in?: number;
}

export class HostedAdminError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string
  ) {
    super(message);
    this.name = "HostedAdminError";
  }
}

function validationLocation(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const parts = value.filter(
    (part): part is string | number =>
      typeof part === "string" || typeof part === "number"
  );
  return parts.length > 0 ? parts.join(".") : null;
}

/**
 * Render FastAPI's string or structured validation detail without coercing an
 * object to "[object Object]". Deliberately ignores `input`, which may contain
 * a credential or other user-supplied secret.
 */
export function formatFastApiDetail(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail.trim();

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (!item || typeof item !== "object") return "";
        const record = item as Record<string, unknown>;
        const message =
          typeof record.msg === "string"
            ? record.msg
            : typeof record.message === "string"
              ? record.message
              : "";
        const location = validationLocation(record.loc);
        return [location, message].filter(Boolean).join(": ");
      })
      .filter(Boolean);
    if (messages.length > 0) return messages.join("; ");
  }

  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    for (const key of ["detail", "message", "error"]) {
      if (key in record) {
        const nested = formatFastApiDetail(record[key]);
        if (nested) return nested;
      }
    }
  }

  return "The Account service rejected the request.";
}

function errorCode(status: number): string {
  if (status === 401) return "UNAUTHORIZED";
  if (status === 403) return "FORBIDDEN";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "CONFLICT";
  if (status === 422) return "VALIDATION_ERROR";
  if (status === 429) return "RATE_LIMITED";
  if (status >= 500) return "SERVICE_UNAVAILABLE";
  return "ADMIN_API_ERROR";
}

async function errorFromResponse(response: Response): Promise<HostedAdminError> {
  let detail: unknown;
  try {
    const contentType = response.headers.get("content-type") || "";
    detail = contentType.includes("json")
      ? (await response.json() as { detail?: unknown; message?: unknown; error?: unknown })
      : await response.text();
  } catch {
    detail = undefined;
  }

  const record =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? detail as Record<string, unknown>
      : null;
  const candidate = record?.detail ?? record?.message ?? record?.error ?? detail;
  return new HostedAdminError(
    response.status,
    errorCode(response.status),
    formatFastApiDetail(candidate)
  );
}

function adminHeaders(adminKey: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Admin-API-Key": adminKey,
  };
}

function endpoint(config: HostedAdminConfig, path: string): string {
  return `${config.baseUrl.replace(/\/$/, "")}${path}`;
}

async function adminRequest<T>(
  config: HostedAdminConfig,
  path: string,
  init: RequestInit = {}
): Promise<{ data: T; status: number }> {
  const fetcher = config.fetcher ?? fetch;
  let response: Response;
  try {
    response = await fetcher(endpoint(config, path), {
      ...init,
      headers: {
        ...adminHeaders(config.adminKey),
        ...init.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new HostedAdminError(
      503,
      "SERVICE_UNAVAILABLE",
      "The Account service is temporarily unavailable."
    );
  }

  if (!response.ok) throw await errorFromResponse(response);
  return { data: await response.json() as T, status: response.status };
}

export async function listHostedTokens(
  config: HostedAdminConfig,
  userId: number
): Promise<HostedTokenMetadata[]> {
  const rows = (
    await adminRequest<Array<Record<string, unknown>>>(
      config,
      `/admin/users/${userId}/tokens`
    )
  ).data;

  return rows.map((row) => ({
    id: String(row.id),
    scopes: Array.isArray(row.scopes)
      ? row.scopes.filter((scope): scope is string => typeof scope === "string")
      : [],
    name: typeof row.name === "string" ? row.name : null,
    created_at: typeof row.created_at === "string" ? row.created_at : null,
    last_used_at:
      typeof row.last_used_at === "string" ? row.last_used_at : null,
    expires_at: typeof row.expires_at === "string" ? row.expires_at : null,
  }));
}

function validationError(message: string): never {
  throw new HostedAdminError(422, "VALIDATION_ERROR", message);
}

function isRecord(input: unknown): input is Record<string, unknown> {
  return Boolean(input) && typeof input === "object" && !Array.isArray(input);
}

function normalizeScopes(raw: unknown): string[] {
  if (!Array.isArray(raw) || raw.some((value) => typeof value !== "string")) {
    validationError("Scopes must be an array of strings.");
  }

  const scopes = raw
    .map((value) => value.trim())
    .filter(Boolean);

  if (scopes.length === 0 || scopes.some((scope) => !VALID_SCOPES.has(scope))) {
    validationError("Choose at least one valid API-key scope.");
  }
  return [...new Set(scopes)];
}

export function supportedMintBody(input: unknown): HostedMintInput {
  if (!isRecord(input)) {
    validationError("The API-key request must be a JSON object.");
  }

  const supported = new Set(["scopes", "name", "expires_in"]);
  const unknownFields = Object.keys(input).filter((key) => !supported.has(key));
  if (unknownFields.length > 0) {
    validationError(
      `Unsupported API-key request field: ${unknownFields.sort().join(", ")}.`
    );
  }

  const body: HostedMintInput = { scopes: normalizeScopes(input.scopes) };

  if (input.name !== undefined) {
    if (typeof input.name !== "string") {
      validationError("Name must be a string.");
    }
    body.name = input.name.trim();
  }

  if (input.expires_in !== undefined) {
    if (
      typeof input.expires_in !== "number" ||
      !Number.isSafeInteger(input.expires_in) ||
      input.expires_in <= 0
    ) {
      validationError("Expiration must be a positive integer of seconds.");
    }
    body.expires_in = input.expires_in;
  }

  return body;
}

export async function mintHostedToken(
  config: HostedAdminConfig,
  userId: number,
  input: unknown
): Promise<HostedMintedToken> {
  return (
    await adminRequest<HostedMintedToken>(
      config,
      `/admin/users/${userId}/tokens`,
      {
        method: "POST",
        body: JSON.stringify(supportedMintBody(input)),
      }
    )
  ).data;
}
