/**
 * types.ts — the brick's request contract, anchored on @vexa/dash-contracts.
 *
 * The dashboard's start-bot request body. `Platform` is imported from the single consumed-contract
 * brick (dash-contracts) so this view shares the EXACT platform vocabulary the rest of the dashboard
 * speaks — the form never invents its own platform strings. The request shape itself mirrors the
 * sealed api.v1 `POST /bots` body (`platform` + `native_meeting_id` are the floor; the rest optional).
 *
 * Type-only import: erased at compile, so the bundled component carries no contract runtime.
 */
import type { Platform } from "@vexa/dash-contracts";

export type { Platform };

/**
 * The body POSTed to create a bot (api.v1 `CreateBotRequest`). The view produces this and hands it to
 * the injected `onSubmit` — it never fetches. `platform` + `native_meeting_id` are required; everything
 * else is optional and only present when the user supplied it.
 */
export interface CreateBotRequest {
  platform: Platform;
  native_meeting_id: string;
  /** Teams/Zoom meetings can carry a passcode; parsed out of the URL when present. */
  passcode?: string;
  /** Preserved original URL for white-label / unrecognized-vendor links (backend Path 3). */
  meeting_url?: string;
  /** The bot's display name in the participant list. */
  bot_name?: string;
}
