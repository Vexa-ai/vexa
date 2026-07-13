/**
 * _host.ts — the ENTIRE contract between the isolated joining layer and the
 * outside world. Every file in this package imports host symbols from HERE and
 * nowhere else. This file imports nothing but Node builtins, so the package has
 * zero edges back into vexa-bot/core. That is the isolation guarantee: cut this
 * one file's deps and the join layer is provably standalone.
 *
 * The monolith previously supplied these via ../../utils, ../../types,
 * ../../index and ./recording. Here they are reduced to: a logger, a tiny
 * config shape, and a Hooks object the embedder installs to observe state.
 */

// ── Config: only the fields the join layer actually reads ──────────────
export interface BotConfig {
  platform?: "google_meet" | "teams" | "zoom" | "jitsi" | string;
  botName?: string;
  /** meeting passcode (zoom passcode screen / jitsi room password) */
  passcode?: string;
  authenticated?: boolean;
  uiInteractionMode?: "humanized" | "synthetic";
  automaticLeave?: {
    waitingRoomTimeout: number;
    noOneJoinedTimeout?: number;
    everyoneLeftTimeout?: number;
  };
  [k: string]: any; // tolerate extra fields the embedder passes through
}

// ── Logger (structured single-line JSON, like the monolith's) ──────────
export function logJSON(obj: Record<string, any>): void {
  try { console.log(JSON.stringify({ ts: new Date().toISOString(), ...obj })); }
  catch { console.log(String(obj?.msg ?? obj)); }
}
export function log(message: string): void { logJSON({ level: "info", msg: message }); }
export function randomDelay(amount: number): number {
  // deterministic-ish jitter; no Math.random dependency required by callers
  return (2 * 0.5 - 1) * (amount / 10) + amount;
}

// ── Hooks: the embedder's window into join-layer state ─────────────────
export type JoinState =
  | "joining" | "awaiting_admission" | "admitted" | "rejected"
  | "blocked" | "leaving" | "needs_human_help";

export interface Hooks {
  onState: (state: JoinState, detail?: any) => void | Promise<void>;
  /** recording is a HOST concern — the join layer never records. */
  onStopRecording: (page: any, botConfig: BotConfig) => void | Promise<void>;
}

const defaultHooks: Hooks = {
  onState: (s, d) => log(`>>> [JOIN-STATE] ${s}${d ? " — " + JSON.stringify(d) : ""}`),
  onStopRecording: () => {},
};
let hooks: Hooks = { ...defaultHooks };
export function setHooks(h: Partial<Hooks>): void { hooks = { ...defaultHooks, ...h }; }

// ── The exact symbols the copied files import ──────────────────────────
export async function callJoiningCallback(_botConfig: BotConfig): Promise<void> {
  await hooks.onState("joining");
}
export async function callAwaitingAdmissionCallback(_botConfig: BotConfig): Promise<void> {
  await hooks.onState("awaiting_admission");
}
export async function callLeaveCallback(_botConfig: BotConfig, ...rest: any[]): Promise<void> {
  await hooks.onState("leaving", rest?.[0]);
}
/**
 * Bot-detection block (reCAPTCHA / blank block page). The join layer does NOT
 * quit — a human via VNC or an agent via CDP may still solve it — but it MUST
 * surface the state so the host stops waiting blind. Emitted once per block.
 */
export async function callBlockedCallback(
  _botConfig: BotConfig, reason: string, detail?: any,
): Promise<void> {
  await hooks.onState("blocked", { reason, ...detail });
}
export async function callNeedsHumanHelpCallback(
  _botConfig: BotConfig, reason?: string, screenshotPath?: string,
): Promise<void> {
  await hooks.onState("needs_human_help", { reason, screenshotPath });
}
export async function stopGoogleRecording(page?: any, botConfig?: BotConfig): Promise<void> {
  await hooks.onStopRecording(page, botConfig as BotConfig);
}
export async function stopTeamsRecording(page?: any, botConfig?: BotConfig): Promise<void> {
  await hooks.onStopRecording(page, botConfig as BotConfig);
}
export async function stopZoomRecording(page?: any, botConfig?: BotConfig): Promise<void> {
  await hooks.onStopRecording(page, botConfig as BotConfig);
}
export async function stopJitsiRecording(page?: any, botConfig?: BotConfig): Promise<void> {
  await hooks.onStopRecording(page, botConfig as BotConfig);
}
