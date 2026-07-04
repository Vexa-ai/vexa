/** Data-access for the API-tokens surface — thin, typed calls to the /api/tokens routes (SoC: the
 *  surface renders, this module fetches). The server scopes everything to the logged-in user; the
 *  client never sends a user_id (P20). Fail-loud via the shared apiClient (P18). */
import { getJson } from "./apiClient";

export const TOKEN_SCOPES = ["bot", "tx", "browser"] as const;
export type TokenScope = (typeof TOKEN_SCOPES)[number];

export interface TokenInfo {
  id: number;
  scopes: string[];
  name?: string | null;
  created_at?: string | null;
  last_used_at?: string | null;
  expires_at?: string | null;
}

/** The mint response — the ONLY time the secret token value reaches the client. */
export interface MintedToken extends TokenInfo {
  token: string;
}

export async function listTokens(): Promise<TokenInfo[]> {
  const { tokens } = await getJson<{ tokens: TokenInfo[] }>("/api/tokens", { cache: "no-store" });
  return tokens;
}

export async function createToken(opts: { scopes: TokenScope[]; name?: string; expiresIn?: number }): Promise<MintedToken> {
  const { token } = await getJson<{ token: MintedToken }>("/api/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  return token;
}

export async function revokeToken(id: number): Promise<void> {
  await getJson<{ success: boolean }>(`/api/tokens/${id}`, { method: "DELETE" });
}
