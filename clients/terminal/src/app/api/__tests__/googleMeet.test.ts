/** Can this deployment create a Google Meet? (Vexa-ai/vexa#1614)
 *
 *  The empty chat's standing act has two branches and the predicate picks which. The point of these
 *  tests is the ASYMMETRY: signing in with Google is not having a calendar, and the live deployment
 *  is exactly that case — `GOOGLE_CLIENT_ID` is set for sign-in and nothing has ever asked for the
 *  `calendar.events` scope. A predicate that answered `true` there would put a chip in front of the
 *  founder offering to make a Meet it cannot make.
 */
import { describe, expect, it } from "vitest";
import { googleMeetConfigured, googleSignInConfigured } from "../googleMeet";

const env = (over: Record<string, string> = {}) => over as unknown as NodeJS.ProcessEnv;

const FULL = {
  GOOGLE_CLIENT_ID: "id", GOOGLE_CLIENT_SECRET: "secret",
  GOOGLE_OAUTH_SCOPES: "openid email profile https://www.googleapis.com/auth/calendar.events",
  GOOGLE_OAUTH_ACCESS_TYPE: "offline",
};

describe("sign-in is not a calendar", () => {
  it("the live shape — an OAuth client for sign-in only — cannot create a Meet", () => {
    const signInOnly = env({ GOOGLE_CLIENT_ID: "id", GOOGLE_CLIENT_SECRET: "secret" });
    expect(googleSignInConfigured(signInOnly)).toBe(true);
    expect(googleMeetConfigured(signInOnly)).toBe(false);
  });

  it("no OAuth client at all is neither", () => {
    expect(googleSignInConfigured(env())).toBe(false);
    expect(googleMeetConfigured(env())).toBe(false);
  });
});

describe("all three pieces, or nothing", () => {
  it("client + calendar scope + offline access", () => {
    expect(googleMeetConfigured(env(FULL))).toBe(true);
  });

  it("the scope without offline access is a Meet only while somebody is signing in", () => {
    expect(googleMeetConfigured(env({ ...FULL, GOOGLE_OAUTH_ACCESS_TYPE: "online" }))).toBe(false);
  });

  it("offline access without the scope is a refresh token for nothing", () => {
    expect(googleMeetConfigured(env({ ...FULL, GOOGLE_OAUTH_SCOPES: "openid email profile" })))
      .toBe(false);
  });

  it("a different calendar scope is not this one — read-only cannot insert an event", () => {
    expect(googleMeetConfigured(env({
      ...FULL, GOOGLE_OAUTH_SCOPES: "https://www.googleapis.com/auth/calendar.readonly",
    }))).toBe(false);
  });
});
