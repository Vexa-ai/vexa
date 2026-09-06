/** Can this deployment CREATE a Google Meet? — the one predicate the standing act reads.
 *
 *  Founder, 2026-09-06 (Vexa-ai/vexa#1614): the empty chat's standing act is *"Create an ad hoc
 *  Google Meet and put Vexa in it"* — *"when a Google account is connected it creates the Meet and
 *  sends the bot in one act; when not, the act is 'connect Google' first, said plainly"*.
 *
 *  TODAY IT IS FALSE, AND THIS FILE SAYS WHY RATHER THAN RETURNING A CONSTANT. Creating a Meet is
 *  `calendar.events.insert` with `conferenceData.createRequest`, and that needs three things this
 *  deployment does not have, in this order:
 *
 *    1. an OAuth client — `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. This one IS set: it is the
 *       sign-in provider (`api/auth/[...nextauth]/authOptions.ts`), and sign-in is all it is used
 *       for. Signing in with Google does not grant a calendar.
 *    2. the CALENDAR SCOPE on that client — `https://www.googleapis.com/auth/calendar.events`,
 *       enabled on the Google Cloud project and asked for at consent. Nothing asks for it.
 *    3. OFFLINE ACCESS — `access_type=offline`, so a refresh token is stored and the Meet can be
 *       created by a server later rather than only in the second the person signs in.
 *
 *  And then the call itself, which is not written: this repository has no Google API client
 *  anywhere (no `googleapis`, no `calendar/v3`). **So a deployment that sets the two variables below
 *  gets a chip that lies.** They are read here so the capability has ONE address — one place for the
 *  act to ask, one place to flip when the call lands — not as a switch anybody should set today.
 */

/** Is Google SIGN-IN configured? The same predicate `authOptions` self-gates its provider on. */
export function googleSignInConfigured(env: NodeJS.ProcessEnv = process.env): boolean {
  return !!(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET);
}

/** Can a Meet be created for this person without asking them to connect anything first? */
export function googleMeetConfigured(env: NodeJS.ProcessEnv = process.env): boolean {
  return googleSignInConfigured(env)
    && (env.GOOGLE_OAUTH_SCOPES || "").includes("calendar.events")
    && (env.GOOGLE_OAUTH_ACCESS_TYPE || "") === "offline";
}
