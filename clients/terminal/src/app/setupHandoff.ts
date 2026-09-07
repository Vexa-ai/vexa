/** THE SETUP HAND-OFF MARKER — one name and one value, shared by both halves of one claim.
 *
 *  The durable record of *this instance's administrator has already been sent into the setup
 *  conversation* lives in the platform-settings `setup` key, field `global`, value `handoff`.
 *  TWO things open that conversation, so two things raise this marker: the claim route, which mints
 *  the arrival scaffold server-side (`api/auth/claim-admin`), and `SetupGate`, which navigates to
 *  `?setup=global` when nothing has been minted. Exactly one thing lowers it — the corner card's
 *  "Open this Vexa", which writes `completed`. Neither opener runs twice, because each reads the
 *  marker before it opens anything.
 *
 *  IT IS A FILE BECAUSE IT WAS NEARLY TWO LITERALS. The same value typed on both sides of a handover
 *  is the shape every silent drift in this client has had — `minutes/arrival.ts` says so about its
 *  own storage key, in almost these words. Here a drift would not throw: it would open a second
 *  chat, look plausible, and cost the admin their first turn (Vexa-ai/vexa#1609).
 *
 *  NO IMPORTS, DELIBERATELY. A server route and a `"use client"` component both read this, and
 *  anything this module pulled in would be pulled into the browser bundle beside it — `adminApi.ts`,
 *  which is where the value would otherwise naturally live, reads the internal secret. */

/** The platform-settings key both halves write. */
export const SETUP_SETTING = "setup" as const;

/** What `setup.global` carries once the administrator has been sent into the setup conversation. */
export const SETUP_HANDOFF = "handoff";

/** The partial update that records it — a fresh object per call, so a shared one can never be
 *  edited under a later caller.
 *
 *  ⚠ EVERY FIELD HERE MUST EXIST IN admin-api's `_SETUP_FIELDS`. A field it does not know is dropped
 *  in silence; a write it understands NOTHING of is refused outright. Both rules were written after
 *  this exact field went missing from that tuple on 2026-09-02, stored nothing, and answered 200 —
 *  see `put_platform_setting` in admin-api's main.py, which carries the story. */
export const setupHandoffUpdate = (): Record<string, string> => ({ global: SETUP_HANDOFF });
