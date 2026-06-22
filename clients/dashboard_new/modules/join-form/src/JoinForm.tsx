/**
 * JoinForm.tsx — the start-bot form (presentational).
 *
 * Props in, DOM out: a platform select, a meeting URL / native-id input, and a bot-name field. On
 * submit it parses the input into (platform, native id) via `parseMeetingInput`, builds a
 * `CreateBotRequest` typed by @vexa/dash-contracts, and hands it to the injected `onSubmit`.
 *
 * No store, no fetch, no websocket — all data is injected and the only output is the `onSubmit` call.
 * The parser auto-detects the platform from the URL; the platform <select> lets the user override (or
 * pick one for a bare native id the parser can't classify). This is the clean modular replacement for
 * the vendored `components/join/join-form.tsx` (which was wired straight to vexaAPI + zustand + toast).
 */
import { useMemo, useState } from "react";
import { parseMeetingInput } from "./parse-meeting-input.js";
import type { CreateBotRequest, Platform } from "./types.js";

export interface JoinFormProps {
  /** Called with the assembled CreateBotRequest when the form is submitted with valid input. */
  onSubmit: (request: CreateBotRequest) => void;
  /** Pre-fills the bot-name field. Defaults to "Vexa". */
  defaultBotName?: string;
}

/** The selectable platforms + their display labels and input placeholders. */
const PLATFORMS: { value: Platform; label: string; placeholder: string }[] = [
  { value: "google_meet", label: "Google Meet", placeholder: "https://meet.google.com/abc-defg-hij" },
  { value: "teams", label: "Microsoft Teams", placeholder: "https://teams.microsoft.com/l/meetup-join/…" },
  { value: "zoom", label: "Zoom", placeholder: "https://zoom.us/j/85173157171" },
];

export function JoinForm({ onSubmit, defaultBotName = "Vexa" }: JoinFormProps) {
  // `platform` is the user's explicit pick; the parser may override it from the URL on submit.
  const [platform, setPlatform] = useState<Platform>("google_meet");
  const [meetingInput, setMeetingInput] = useState("");
  const [botName, setBotName] = useState(defaultBotName);
  const [error, setError] = useState<string | null>(null);

  const placeholder = useMemo(
    () => PLATFORMS.find((p) => p.value === platform)?.placeholder ?? "",
    [platform],
  );

  // Live parse for the inline hint (does not block typing).
  const parsed = useMemo(() => parseMeetingInput(meetingInput), [meetingInput]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const result = parseMeetingInput(meetingInput);
    if (!result || !result.nativeId) {
      setError("Enter a valid meeting URL or ID");
      return;
    }
    setError(null);

    const request: CreateBotRequest = {
      // the URL is canonical for the platform when the parser recognized it; otherwise honor the pick
      platform: result.platform,
      native_meeting_id: result.nativeId,
    };
    if (result.passcode) request.passcode = result.passcode;
    if (result.originalUrl) request.meeting_url = result.originalUrl;
    const name = botName.trim();
    if (name) request.bot_name = name;

    onSubmit(request);
  }

  return (
    <form className="join-form" onSubmit={handleSubmit} aria-label="Start a meeting bot">
      <fieldset className="join-form__platform">
        <label htmlFor="join-platform">Platform</label>
        <select
          id="join-platform"
          name="platform"
          value={platform}
          onChange={(e) => setPlatform(e.target.value as Platform)}
        >
          {PLATFORMS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </fieldset>

      <div className="join-form__meeting">
        <label htmlFor="join-meeting">Meeting URL or ID</label>
        <input
          id="join-meeting"
          name="meeting"
          type="text"
          placeholder={placeholder}
          value={meetingInput}
          onChange={(e) => {
            setMeetingInput(e.target.value);
            if (error) setError(null);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby="join-meeting-hint"
        />
        <p id="join-meeting-hint" className="join-form__hint" role={error ? "alert" : undefined}>
          {error
            ? error
            : parsed
              ? `${labelFor(parsed.platform)} · ${parsed.nativeId}`
              : "Paste the meeting link or enter the meeting ID"}
        </p>
      </div>

      <div className="join-form__name">
        <label htmlFor="join-bot-name">Bot name</label>
        <input
          id="join-bot-name"
          name="bot_name"
          type="text"
          value={botName}
          onChange={(e) => setBotName(e.target.value)}
        />
      </div>

      <button type="submit" className="join-form__submit">
        Start bot
      </button>
    </form>
  );
}

function labelFor(platform: Platform): string {
  return PLATFORMS.find((p) => p.value === platform)?.label ?? platform;
}
