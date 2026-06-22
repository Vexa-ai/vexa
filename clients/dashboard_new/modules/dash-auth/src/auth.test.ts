/**
 * auth.test — L2: the SESSION/TOKEN core over the in-memory fake backend. Exit 1 on any failure.
 *
 * Drives the REAL createAuthSession against createFakeAuthBackend to pin the contract the dashboard
 * depends on:
 *   1. self-host: createAuthSession({ selfHostToken }) → isAuthenticated() true, getToken() === selfHostToken
 *      (no login needed; the token IS the identity), getUser() null (self-host carries no user).
 *   2. loginMagicLink(valid): verify → find-or-create → mint → session token set, getUser() === the user,
 *      getToken() is the minted session token (NOT the selfHostToken), and an invalid token throws.
 *   3. loginOAuth: exchange code → find-or-create + mint. A SEEDED email resolves the existing user
 *      (find branch); an unseen email creates one (create branch). getToken() is the minted token.
 *   4. logout: clears session token + user → getToken() falls back to selfHostToken (or null when none),
 *      getUser() null, isAuthenticated() follows the fallback.
 *
 * No assertion lib — tsx + exit code, same shape as the sibling dash-* bricks' *.test.ts.
 */
import { createAuthSession } from "./index.js";
import { createFakeAuthBackend } from "./fakes.js";

let failed = 0;
const ok = (label: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${label}${cond ? "" : detail ? " — " + detail : ""}`);
  if (!cond) failed++;
};

async function main() {
  // ── 1. self-host token resolution ────────────────────────────────────────────────────────────
  {
    const session = createAuthSession({
      backend: createFakeAuthBackend(),
      selfHostToken: "selfhost-key-123",
    });
    ok("self-host: isAuthenticated() true before any login", session.isAuthenticated());
    ok(
      "self-host: getToken() === selfHostToken",
      session.getToken() === "selfhost-key-123",
      `got ${session.getToken()}`,
    );
    ok("self-host: getUser() null (token is the identity)", session.getUser() === null);
    const resolved = await session.loginSelfHost();
    ok("self-host: loginSelfHost() returns the resolved token", resolved === "selfhost-key-123");
  }

  // ── no selfHostToken, no login → unauthenticated ────────────────────────────────────────────────
  {
    const session = createAuthSession({ backend: createFakeAuthBackend() });
    ok("no token: isAuthenticated() false", !session.isAuthenticated());
    ok("no token: getToken() null", session.getToken() === null);
  }

  // ── 2. magic-link login over the fake backend ───────────────────────────────────────────────────
  {
    const backend = createFakeAuthBackend();
    const session = createAuthSession({ backend });

    const user = await session.loginMagicLink("magic:anna@vexa.ai");
    ok("magic-link: returns the user", user.email === "anna@vexa.ai", `got ${user.email}`);
    ok("magic-link: isAuthenticated() true after login", session.isAuthenticated());
    ok("magic-link: getUser() === the logged-in user", session.getUser()?.email === "anna@vexa.ai");
    const tok = session.getToken();
    ok(
      "magic-link: getToken() is a freshly minted session token",
      !!tok && tok.startsWith("tok-") && tok.includes(user.id),
      `got ${tok}`,
    );

    // a session token must WIN over a configured selfHostToken
    const dual = createAuthSession({ backend, selfHostToken: "selfhost-key-123" });
    await dual.loginMagicLink("magic:ben@vexa.ai");
    ok(
      "magic-link: session token wins over selfHostToken",
      dual.getToken() !== "selfhost-key-123" && dual.getToken()!.startsWith("tok-"),
      `got ${dual.getToken()}`,
    );

    // invalid magic-link token throws (backend.verifyMagicLink rejects)
    let threw = false;
    try {
      await createAuthSession({ backend }).loginMagicLink("not-a-magic-token");
    } catch {
      threw = true;
    }
    ok("magic-link: invalid token throws", threw);
  }

  // ── 3. OAuth login: find-or-create ──────────────────────────────────────────────────────────────
  {
    // create branch: unseen email → a new user is provisioned
    const backendCreate = createFakeAuthBackend();
    const sCreate = createAuthSession({ backend: backendCreate });
    const created = await sCreate.loginOAuth("google", "google:newcomer@vexa.ai");
    ok("oauth(create): provisions a new user", created.email === "newcomer@vexa.ai");
    ok("oauth(create): getToken() is a minted token", sCreate.getToken()?.startsWith("tok-") === true);

    // find branch: seeded email → the EXISTING user is reused (same id, not a fresh one)
    const backendFind = createFakeAuthBackend({
      users: [{ id: "usr-anna", email: "anna@vexa.ai", name: "Anna Existing" }],
    });
    const sFind = createAuthSession({ backend: backendFind });
    const found = await sFind.loginOAuth("google", "google:anna@vexa.ai");
    ok("oauth(find): resolves the existing user (id)", found.id === "usr-anna", `got ${found.id}`);
    ok("oauth(find): keeps the existing name", found.name === "Anna Existing", `got ${found.name}`);
    ok("oauth(find): getUser() === existing user", sFind.getUser()?.id === "usr-anna");

    // a provider mismatch on the code throws (exchangeOAuth rejects)
    let threw = false;
    try {
      await createAuthSession({ backend: createFakeAuthBackend() }).loginOAuth(
        "google",
        "azure-ad:x@vexa.ai",
      );
    } catch {
      threw = true;
    }
    ok("oauth: provider/code mismatch throws", threw);
  }

  // ── 4. logout → token null (or selfHostToken fallback) ──────────────────────────────────────────
  {
    const backend = createFakeAuthBackend();
    const session = createAuthSession({ backend });
    await session.loginMagicLink("magic:carol@vexa.ai");
    ok("logout: authenticated before logout", session.isAuthenticated());

    await session.logout();
    ok("logout: getToken() null after logout (no selfHostToken)", session.getToken() === null);
    ok("logout: getUser() null after logout", session.getUser() === null);
    ok("logout: isAuthenticated() false after logout", !session.isAuthenticated());

    // logout clears the SESSION token; a configured selfHostToken still resolves afterwards
    const dual = createAuthSession({ backend, selfHostToken: "selfhost-key-123" });
    await dual.loginMagicLink("magic:dave@vexa.ai");
    ok("logout(dual): session token active before logout", dual.getToken()!.startsWith("tok-"));
    await dual.logout();
    ok(
      "logout(dual): falls back to selfHostToken after logout",
      dual.getToken() === "selfhost-key-123",
      `got ${dual.getToken()}`,
    );
    ok("logout(dual): still authenticated via selfHostToken", dual.isAuthenticated());
  }

  if (failed) {
    console.log(`\n❌ FAIL — ${failed} assertion(s) failed`);
    process.exit(1);
  }
  console.log(
    "\n✅ PASS — self-host token resolves; magic-link + OAuth do find-or-create→mint and set the session token; session token wins over selfHostToken; logout clears to null / falls back to selfHostToken",
  );
  process.exit(0);
}

main().catch((e) => {
  console.error("❌ FAIL —", e?.message || e);
  process.exit(1);
});
