/** admin-api's single-use record for emailed sign-in links (`POST /internal/signin-links/redeem`), as
 *  the terminal's tests see it: the first redeem of a jti is `first`, every later one `used`, and
 *  `down` answers `unavailable` like an unreachable admin-api. One store, two ways in:
 *    - `redeem()` stands in for `adminApi.redeemSigninLink` in a test that mocks that module;
 *    - `respond()` answers the HTTP call in a test that drives the real client through a fetch stub.
 */
export const linkLedger = {
  used: new Set<string>(),
  down: false,
  reset(): void {
    this.used.clear();
    this.down = false;
  },
  async redeem(jti: string, _expiresAt?: number): Promise<"first" | "used" | "unavailable"> {
    if (this.down) return "unavailable";
    if (this.used.has(jti)) return "used";
    this.used.add(jti);
    return "first";
  },
  isRedeem(url: string): boolean {
    return String(url).includes("/internal/signin-links/redeem");
  },
  async respond(init?: RequestInit): Promise<Response> {
    if (this.down) return new Response("sign-in link record unavailable", { status: 503 });
    const { jti } = JSON.parse(String(init?.body ?? "{}")) as { jti?: string };
    if (!jti || this.used.has(jti)) return new Response(JSON.stringify({ detail: "already used" }), { status: 409 });
    this.used.add(jti);
    return new Response(JSON.stringify({ first: true }), { status: 200 });
  },
};
