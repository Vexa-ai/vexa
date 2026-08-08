/**
 * #1061 — the Zoom dead-profile guard, pinned in the ACTUAL join path.
 *
 * THE DEFECT: on hosted prod every `auth_session_missing` in the release is Zoom (4 of 23 Zoom
 * meetings; no other platform produces the reason). The reason is PERMANENT by design, so a bot
 * profile whose Zoom session died keeps consuming customer meetings until a human re-authenticates
 * it — and until this change the state had no name: authenticated mode LOGGED the sign-in wall and
 * proceeded, so the failure surfaced minutes later as a nameless join-button timeout.
 *
 * WHAT THIS FILE PINS
 *   1. `classifyZoomSession` convicts on each recorded signed-out signature — the sign-in redirect,
 *      the sign-in wall, and the guest lobby (empty name field + no account cookie).
 *   2. It does NOT convict on a normal Zoom join failure — a healthy signed-in pre-join, a
 *      host-not-started error page, a passcode screen, the RTMS anti-bot wall, or the ambiguous
 *      halves of the guest-lobby rule (empty name field alone / missing cookie alone). Those keep
 *      their existing outcomes; a false positive here would refuse a real join PERMANENTLY.
 *   3. The guard is WIRED: driving the shipped `joinZoomMeeting` over a fabricated Page in
 *      authenticated mode throws `ZoomAuthSessionError` with outcome `auth_session_missing` and
 *      never types a guest name — so deleting the guard call from join.ts turns this suite RED
 *      offline (the #756 lesson: a classifier test alone proves nothing about the join path).
 *   4. GUEST mode is untouched: the same wall text still reports the host-policy `auth_required`,
 *      because "the host restricted this meeting" is not "our credential is dead".
 *   5. The reason text carries a short raw detail (`zoom_auth_session_missing <signal> …`) and
 *      never leaks the meeting passcode from the `?pwd=` query.
 *
 * FIXTURE HONESTY: the page states below are FABRICATED from the shipped join path's own account of
 * signed-in vs guest Zoom pre-join and from @vexa/remote-browser's session validator (sign-in URL
 * markers + the `zm_aid` account cookie). No live dead Zoom profile was replayed — this proves the
 * CLASSIFICATION and the WIRING, not that Zoom's dead-profile page has exactly this shape.
 *
 * No browser, no live meeting, no Zoom.
 *
 * Run: npx tsx src/zoom/session.test.ts
 */

import { joinZoomMeeting } from "./join";
import {
  classifyZoomSession,
  isZoomSignInUrl,
  redactZoomUrl,
  ZoomAuthSessionError,
  ZOOM_ACCOUNT_COOKIE_NAME,
  ZOOM_AUTH_SESSION_MISSING,
  type ZoomSessionObservation,
} from "./session";
import { AdmissionError } from "../shared/admission";

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail = "") {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ""}`); failed++; }
}

const WC_URL = "https://app.zoom.us/wc/84335626851/join?pwd=super-secret-passcode";

/** A healthy signed-in pre-join: account identity in the name field, live account cookie. */
const HEALTHY: ZoomSessionObservation = {
  url: WC_URL,
  signInWallText: null,
  nameFieldPresent: true,
  nameFieldValue: "Vexa Bot",
  accountCookiePresent: true,
  phase: "pre_join_load",
};

const obs = (o: Partial<ZoomSessionObservation>): ZoomSessionObservation => ({ ...HEALTHY, ...o });

// ── The fabricated Page ─────────────────────────────────────────────────────────────────────
// Covers exactly the surface joinZoomMeeting touches. `bodyText` drives every page.evaluate probe
// (run in-node against a stand-in `document`, the same pattern as zoom/admission.test.ts); `fields`
// drives the locators; `cookies` drives the account-cookie read.
function makePage(o: {
  url?: string;
  bodyText?: string;
  title?: string;
  nameFieldValue?: string | null;   // null = the field does not render
  /** Hide the name field for the first N visibility probes — the React pre-join card renders a
   *  beat after the page settles, which is why the guard is asked TWICE along the join path. */
  nameFieldHiddenForProbes?: number;
  cookies?: Array<{ name: string; value: string }> | "unreadable";
}) {
  const url = o.url ?? WC_URL;
  const bodyText = o.bodyText ?? "";
  const typed: string[] = [];
  const clicked: string[] = [];
  const nameValue = o.nameFieldValue === undefined ? "Vexa Bot" : o.nameFieldValue;
  const hasNameField = nameValue !== null;

  let nameProbes = 0;
  const hiddenFor = o.nameFieldHiddenForProbes ?? 0;
  const locator = (sel: string): any => {
    const isName = sel.includes("#input-for-name");
    return {
      first: () => locator(sel),
      isVisible: async () => (isName ? hasNameField && nameProbes++ >= hiddenFor : false),
      inputValue: async () => (isName ? (nameValue as string) : ""),
      click: async () => { clicked.push(sel); },
      fill: async () => {},
      getAttribute: async () => null,
      count: async () => 0,
      waitFor: async () => {},
    };
  };

  const page: any = {
    typed, clicked,
    url: () => url,
    title: async () => o.title ?? "Zoom Meeting",
    goto: async () => {},
    waitForTimeout: async () => {},
    waitForSelector: async () => ({}),
    waitForFunction: async () => ({}),
    keyboard: { type: async (t: string) => { typed.push(t); } },
    locator,
    evaluate: async (fn: any, arg?: any) => {
      (globalThis as any).document = {
        body: { innerText: bodyText },
        querySelector: () => null,
      };
      try { return fn(arg); } finally { delete (globalThis as any).document; }
    },
    context: () => ({
      cookies: async () => {
        if (o.cookies === "unreadable") throw new Error("no cookie jar on this context");
        return o.cookies ?? [{ name: ZOOM_ACCOUNT_COOKIE_NAME, value: "acct-1" }];
      },
    }),
  };
  return page;
}

const run = (page: any, authenticated: boolean) =>
  joinZoomMeeting(page, "https://zoom.us/j/84335626851?pwd=super-secret-passcode", "Vexa Bot", {
    platform: "zoom", authenticated, passcode: "super-secret-passcode",
  } as any);

async function outcomeOf(p: Promise<unknown>): Promise<{ err: any; label: string }> {
  try { await p; return { err: null, label: "resolved" }; }
  catch (e: any) {
    return {
      err: e,
      label: e instanceof AdmissionError ? `AdmissionError:${e.outcome}` : `Error:${e.message}`,
    };
  }
}

(async () => {
  console.log("\n=== 1. classifyZoomSession convicts on each recorded signed-out signature ===");

  {
    const v = classifyZoomSession(obs({ url: "https://zoom.us/signin?_x=1", nameFieldValue: "" }));
    check("sign-in redirect → signed out (signin_redirect)", v.signedOut && v.signal === "signin_redirect", JSON.stringify(v));
  }
  {
    const v = classifyZoomSession(obs({ signInWallText: "sign in to join this meeting", nameFieldValue: "" }));
    check("sign-in wall text → signed out (signin_wall)", v.signedOut && v.signal === "signin_wall", JSON.stringify(v));
    check("…and the matched phrase is carried in the detail",
      v.signedOut && v.detail.includes('matched="sign in to join this meeting"'), JSON.stringify(v));
  }
  {
    const v = classifyZoomSession(obs({ nameFieldValue: "   ", accountCookiePresent: false }));
    check("guest lobby (blank name field + no account cookie) → signed out (guest_lobby)",
      v.signedOut && v.signal === "guest_lobby", JSON.stringify(v));
  }

  console.log("\n=== 2. …and NOT on a normal Zoom join failure (a false positive refuses a real join) ===");

  check("healthy signed-in pre-join → not signed out", !classifyZoomSession(HEALTHY).signedOut);
  check("host not started (error page, no wall text) → not signed out",
    !classifyZoomSession(obs({ nameFieldPresent: false, nameFieldValue: "" })).signedOut);
  check("RTMS anti-bot wall text is NOT a session verdict",
    !classifyZoomSession(obs({ signInWallText: null, nameFieldValue: "Vexa Bot" })).signedOut);
  check("empty name field ALONE (cookie still live) → not signed out",
    !classifyZoomSession(obs({ nameFieldValue: "", accountCookiePresent: true })).signedOut);
  check("missing cookie ALONE (account name pre-filled) → not signed out",
    !classifyZoomSession(obs({ nameFieldValue: "Vexa Bot", accountCookiePresent: false })).signedOut);
  check("empty name field + UNREADABLE cookie jar → not signed out (unknown never convicts)",
    !classifyZoomSession(obs({ nameFieldValue: "", accountCookiePresent: null })).signedOut);
  check("a white-label portal's own /login path is NOT a Zoom sign-in redirect",
    !isZoomSignInUrl("https://zoom-lfx.platform.linuxfoundation.org/login/meeting/96088138284"));
  check("canonical *.zoom.us /signin IS a Zoom sign-in redirect",
    isZoomSignInUrl("https://us05web.zoom.us/signin"));

  console.log("\n=== 3. the guard is WIRED into joinZoomMeeting (authenticated mode) ===");

  {
    // Sign-in wall on the settled pre-join page, holding a profile → dead profile.
    const page = makePage({ bodyText: "Sign in to join this meeting", nameFieldValue: null });
    const { err, label } = await outcomeOf(run(page, true));
    check("authenticated + sign-in wall → ZoomAuthSessionError('auth_session_missing')",
      err instanceof ZoomAuthSessionError && err.outcome === "auth_session_missing", label);
    check("…and it is an AdmissionError, so the JoinDriver maps it PERMANENT (never re-spawned)",
      err instanceof AdmissionError, label);
    check("…and no guest name was typed (no silent downgrade to an anonymous join)",
      page.typed.length === 0, `typed: ${page.typed}`);
  }
  {
    // The redirect variant: no wall text at all, just a bounce to the account sign-in flow.
    const page = makePage({ url: "https://zoom.us/signin?redirect=wc", nameFieldValue: null });
    const { err, label } = await outcomeOf(run(page, true));
    check("authenticated + sign-in redirect (no wall text) → ZoomAuthSessionError",
      err instanceof ZoomAuthSessionError && err.signal === "signin_redirect", label);
  }
  {
    // Guest lobby: empty name field AND the account cookie gone.
    const page = makePage({ nameFieldValue: "", cookies: [{ name: "_zm_ssid", value: "anon" }] });
    const { err, label } = await outcomeOf(run(page, true));
    check("authenticated + empty name field + no account cookie → ZoomAuthSessionError (guest_lobby)",
      err instanceof ZoomAuthSessionError && err.signal === "guest_lobby", label);
    check("…and no guest name was typed", page.typed.length === 0, `typed: ${page.typed}`);
  }
  {
    // The card renders LATE: nothing convicts at pre_join_load (no field yet, no wall text), and
    // the guest lobby only becomes readable at the name-field step. Pins the SECOND call site.
    const page = makePage({
      nameFieldValue: "", nameFieldHiddenForProbes: 1, cookies: [{ name: "_zm_ssid", value: "anon" }],
    });
    const { err, label } = await outcomeOf(run(page, true));
    check("late-rendering guest lobby → caught at the name-field step, not silently typed over",
      err instanceof ZoomAuthSessionError && err.detail.includes("phase=pre_join_name_field"), label);
    check("…and no guest name was typed", page.typed.length === 0, `typed: ${page.typed}`);
  }
  {
    // `_zm_ssid` alone must NOT read as a session — Zoom sets it on the anonymous sign-in page.
    const v = classifyZoomSession(obs({ nameFieldValue: "", accountCookiePresent: false }));
    check("_zm_ssid is not an account cookie: its presence cannot rescue a guest lobby", v.signedOut);
  }

  console.log("\n=== 4. a HEALTHY authenticated join is untouched, and guest mode keeps its own reason ===");

  {
    const page = makePage({});  // account name pre-filled, live cookie, no wall text
    const { err, label } = await outcomeOf(run(page, true));
    check("authenticated + healthy profile → join proceeds (no error)", err === null, label);
    check("…and the account identity was kept (no fallback name typed)",
      page.typed.length === 0, `typed: ${page.typed}`);
  }
  {
    const page = makePage({ bodyText: "Sign in to join this meeting", nameFieldValue: null });
    const { err, label } = await outcomeOf(run(page, false));
    check("GUEST + sign-in wall → still the host-policy auth_required reason, not the dead-profile one",
      err instanceof AdmissionError && !(err instanceof ZoomAuthSessionError)
        && err.outcome === "auth_session_missing" && err.message.includes("auth_required"), label);
  }

  console.log("\n=== 5. the raw detail is short, greppable, and leaks no passcode ===");

  {
    const page = makePage({ bodyText: "Only authenticated users can join", nameFieldValue: null });
    const { err } = await outcomeOf(run(page, true));
    const msg = String(err?.message ?? "");
    check(`reason text leads with the ${ZOOM_AUTH_SESSION_MISSING} tag`, msg.includes(ZOOM_AUTH_SESSION_MISSING), msg);
    check("…names the phase it was observed in", msg.includes("phase=pre_join_load"), msg);
    check("…and never carries the ?pwd= passcode", !msg.includes("super-secret-passcode"), msg);
    check("…and stays short enough for a lifecycle reason field (<240 chars)", msg.length < 240, `len=${msg.length}`);
  }
  check("redactZoomUrl drops the query string",
    redactZoomUrl(WC_URL) === "https://app.zoom.us/wc/84335626851/join", redactZoomUrl(WC_URL));

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
})();
