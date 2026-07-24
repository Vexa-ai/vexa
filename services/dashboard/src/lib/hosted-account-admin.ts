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

export interface HostedUser {
  id: number;
  email: string;
  name?: string | null;
  max_concurrent_bots: number;
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
  scopes?: unknown;
  scope?: unknown;
  name?: unknown;
  expires_in?: unknown;
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

export async function resolveHostedUser(
  config: HostedAdminConfig,
  email: string
): Promise<HostedUser> {
  try {
    return (
      await adminRequest<HostedUser>(
        config,
        `/admin/users/email/${encodeURIComponent(email)}`
      )
    ).data;
  } catch (error) {
    if (!(error instanceof HostedAdminError) || error.status !== 404) throw error;
  }

  return (
    await adminRequest<HostedUser>(config, "/admin/users", {
      method: "POST",
      body: JSON.stringify({ email }),
    })
  ).data;
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

function normalizeScopes(input: HostedMintInput): string[] {
  const raw = input.scopes ?? input.scope;
  const values = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
      ? raw.split(",")
      : [];
  const scopes = values
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter(Boolean);

  if (scopes.length === 0 || scopes.some((scope) => !VALID_SCOPES.has(scope))) {
    throw new HostedAdminError(
      422,
      "VALIDATION_ERROR",
      "Choose at least one valid API-key scope."
    );
  }
  return [...new Set(scopes)];
}

export function supportedMintBody(input: HostedMintInput): {
  scopes: string[];
  name?: string;
  expires_in?: number;
} {
  const body: {
    scopes: string[];
    name?: string;
    expires_in?: number;
  } = { scopes: normalizeScopes(input) };

  if (typeof input.name === "string" && input.name.trim()) {
    body.name = input.name.trim();
  }

  if (input.expires_in !== undefined && input.expires_in !== null) {
    const expiresIn =
      typeof input.expires_in === "number"
        ? input.expires_in
        : Number(input.expires_in);
    if (!Number.isInteger(expiresIn) || expiresIn <= 0) {
      throw new HostedAdminError(
        422,
        "VALIDATION_ERROR",
        "Expiration must be a positive number of seconds."
      );
    }
    body.expires_in = expiresIn;
  }

  return body;
}

export async function mintHostedToken(
  config: HostedAdminConfig,
  userId: number,
  input: HostedMintInput
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
