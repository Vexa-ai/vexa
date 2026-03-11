/**
 * Vexa Admin API client with robust error handling
 */

const DEFAULT_TIMEOUT = 15000; // 15 seconds

export interface ApiError {
  code: string;
  message: string;
  details?: string;
  status: number;
}

export interface ApiResult<T> {
  success: boolean;
  data?: T;
  error?: ApiError;
}

/**
 * Parse error response from Vexa API
 */
async function parseErrorResponse(response: Response): Promise<ApiError> {
  const status = response.status;
  let details = "";

  try {
    const text = await response.text();
    try {
      const json = JSON.parse(text);
      details = json.detail || json.message || json.error || text;
    } catch {
      details = text;
    }
  } catch {
    details = "Could not read error response";
  }

  // Map status codes to user-friendly messages
  switch (status) {
    case 401:
      return {
        code: "UNAUTHORIZED",
        message: "Invalid API credentials. Check your Admin API key.",
        details,
        status,
      };
    case 403:
      return {
        code: "FORBIDDEN",
        message: "Access denied. Your API key may not have sufficient permissions.",
        details,
        status,
      };
    case 404:
      return {
        code: "NOT_FOUND",
        message: "Resource not found",
        details,
        status,
      };
    case 409:
      return {
        code: "CONFLICT",
        message: "Resource already exists",
        details,
        status,
      };
    case 422:
      return {
        code: "VALIDATION_ERROR",
        message: "Invalid data provided",
        details,
        status,
      };
    case 429:
      return {
        code: "RATE_LIMITED",
        message: "Too many requests. Please try again later.",
        details,
        status,
      };
    case 500:
      return {
        code: "SERVER_ERROR",
        message: "Vexa API server error. Please try again later.",
        details,
        status,
      };
    case 502:
    case 503:
    case 504:
      return {
        code: "SERVICE_UNAVAILABLE",
        message: "Vexa API is temporarily unavailable. Please try again later.",
        details,
        status,
      };
    default:
      return {
        code: "UNKNOWN_ERROR",
        message: `API request failed with status ${status}`,
        details,
        status,
      };
  }
}

/**
 * Make a request to Vexa Admin API with proper error handling
 */
async function adminRequest<T>(
  path: string,
  options: RequestInit = {},
  timeout = DEFAULT_TIMEOUT
): Promise<ApiResult<T>> {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || process.env.VEXA_API_URL || "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY || VEXA_ADMIN_API_KEY === "your_admin_api_key_here") {
    return {
      success: false,
      error: {
        code: "NOT_CONFIGURED",
        message: "Admin API key is not configured",
        status: 500,
      },
    };
  }

  const url = `${VEXA_ADMIN_API_URL}${path}`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    const response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        ...options.headers,
      },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const error = await parseErrorResponse(response);
      return { success: false, error };
    }

    // Handle empty responses (204 No Content)
    if (response.status === 204) {
      return { success: true };
    }

    const data = await response.json();
    return { success: true, data };
  } catch (error) {
    const err = error as Error;

    // Handle timeout/abort
    if (err.name === "AbortError") {
      return {
        success: false,
        error: {
          code: "TIMEOUT",
          message: "Request timed out. Vexa API may be slow or unreachable.",
          status: 408,
        },
      };
    }

    // Handle network errors
    if (err.message.includes("fetch") || err.message.includes("network") || err.message.includes("ENOTFOUND")) {
      return {
        success: false,
        error: {
          code: "NETWORK_ERROR",
          message: "Cannot reach Vexa API. Check your network connection and VEXA_API_URL.",
          details: err.message,
          status: 0,
        },
      };
    }

    // Handle DNS errors
    if (err.message.includes("getaddrinfo")) {
      return {
        success: false,
        error: {
          code: "DNS_ERROR",
          message: "Cannot resolve Vexa API hostname. Check VEXA_API_URL configuration.",
          details: err.message,
          status: 0,
        },
      };
    }

    // Handle connection refused
    if (err.message.includes("ECONNREFUSED")) {
      return {
        success: false,
        error: {
          code: "CONNECTION_REFUSED",
          message: "Connection refused. Vexa API may not be running.",
          details: err.message,
          status: 0,
        },
      };
    }

    return {
      success: false,
      error: {
        code: "UNKNOWN_ERROR",
        message: "An unexpected error occurred",
        details: err.message,
        status: 0,
      },
    };
  }
}

// ============================================================================
// User API
// ============================================================================

export interface VexaUserData {
  id: string;
  email: string;
  name: string;
  max_concurrent_bots: number;
  created_at: string;
  data?: Record<string, unknown>;
}

/**
 * Find user by email
 */
export async function findUserByEmail(email: string): Promise<ApiResult<VexaUserData>> {
  return adminRequest<VexaUserData>(`/admin/users/email/${encodeURIComponent(email)}`);
}

/**
 * Create a new user
 */
export async function createUser(data: {
  email: string;
  name?: string;
  max_concurrent_bots?: number;
}): Promise<ApiResult<VexaUserData>> {
  return adminRequest<VexaUserData>("/admin/users", {
    method: "POST",
    body: JSON.stringify({
      email: data.email,
      name: data.name || data.email.split("@")[0],
      max_concurrent_bots: data.max_concurrent_bots ?? 3,
    }),
  });
}

/**
 * Create API token for user
 */
export async function createUserToken(userId: string): Promise<ApiResult<{ token: string }>> {
  return adminRequest<{ token: string }>(`/admin/users/${userId}/tokens`, {
    method: "POST",
  });
}

/**
 * Get user by ID (includes data and possibly API tokens depending on backend config)
 */
export async function getUserById(userId: string): Promise<ApiResult<VexaUserData>> {
  return adminRequest<VexaUserData>(`/admin/users/${encodeURIComponent(userId)}`);
}

/**
 * Update user fields (used for writing zoom oauth data into user.data)
 */
export async function updateUser(
  userId: string,
  payload: {
    name?: string;
    image_url?: string;
    max_concurrent_bots?: number;
    data?: Record<string, unknown>;
  }
): Promise<ApiResult<VexaUserData>> {
  return adminRequest<VexaUserData>(`/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// ============================================================================
// Health Check
// ============================================================================

/**
 * Check if Admin API is reachable and properly configured
 */
export async function checkAdminApiHealth(): Promise<ApiResult<{ reachable: boolean }>> {
  const result = await adminRequest<VexaUserData[]>("/admin/users?limit=1");

  if (result.success) {
    return { success: true, data: { reachable: true } };
  }

  // 401/403 means API is reachable but credentials are wrong
  if (result.error?.status === 401 || result.error?.status === 403) {
    return {
      success: false,
      error: {
        ...result.error,
        message: "Vexa API is reachable but Admin API key is invalid",
      },
    };
  }

  return {
    success: false,
    error: result.error,
  };
}
