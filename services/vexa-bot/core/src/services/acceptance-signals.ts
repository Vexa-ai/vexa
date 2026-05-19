import { Page } from "playwright";
import { BotConfig } from "../types";
import { log } from "../utils";
import { callStatusChangeCallback } from "./unified-callback";

export async function collectAcceptanceSignals(page: Page, botConfig: BotConfig): Promise<Record<string, any>> {
  const botName = botConfig.botName || "";
  const observed = await page.evaluate((name) => {
    const existing = { ...((window as any).__vexaAcceptanceSignals || {}) };
    const participantNames = typeof (window as any).getTeamsActiveParticipants === "function"
      ? (window as any).getTeamsActiveParticipants()
      : [];
    const visibleText = Array.from(document.querySelectorAll('[aria-label], [role="menuitem"], [role="button"], [data-tid]'))
      .map((el) => `${el.getAttribute("aria-label") || ""} ${(el.textContent || "")}`.trim())
      .filter(Boolean);
    const selfVisible = !!name && (
      participantNames.includes(name) ||
      visibleText.some((text) => text.includes(name))
    );

    return {
      ...existing,
      self_visible_in_roster: !!(existing.self_visible_in_roster || selfVisible),
      participant_count: typeof (window as any).getTeamsActiveParticipantsCount === "function"
        ? (window as any).getTeamsActiveParticipantsCount()
        : existing.participant_count,
      participant_roster_sample: participantNames.slice(0, 25),
      captions_dom_present: !!document.querySelector('[data-tid="closed-caption-renderer-wrapper"], [data-tid="closed-caption-text"]'),
    };
  }, botName);

  return {
    schema_version: 1,
    platform: botConfig.platform,
    bot_name: botConfig.botName,
    observed_at: new Date().toISOString(),
    configured_capabilities: {
      transcribe_enabled: botConfig.transcribeEnabled !== false,
      voice_agent_enabled: !!botConfig.voiceAgentEnabled,
      recording_enabled: !!botConfig.recordingEnabled,
      video_receive_enabled: !!botConfig.videoReceiveEnabled,
      camera_enabled: !!botConfig.cameraEnabled,
    },
    observed,
  };
}

export function startTeamsAcceptanceSignalsHeartbeat(page: Page, botConfig: BotConfig): () => void {
  let stopped = false;
  let inFlight = false;

  const send = async () => {
    if (stopped || inFlight || page.isClosed()) return;
    inFlight = true;
    try {
      const signals = await collectAcceptanceSignals(page, botConfig);
      await callStatusChangeCallback(
        botConfig,
        "active",
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        signals
      );
      log("[Acceptance] raw signals heartbeat sent");
    } catch (err: any) {
      log(`[Acceptance] raw signals heartbeat failed (non-fatal): ${err?.message || String(err)}`);
    } finally {
      inFlight = false;
    }
  };

  setTimeout(send, 5000);
  const interval = setInterval(send, 30000);
  return () => {
    stopped = true;
    clearInterval(interval);
  };
}
