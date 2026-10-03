/**
 * room-identity — is this speaker a MEETING ROOM or a person?
 *
 * A room system (Google Meet hardware, a Microsoft Teams Room, a Zoom Room) mixes every microphone
 * INSIDE the room hardware, before the call. What joins the meeting is one participant, one audio
 * stream, one display name — so every human in that room is attributed to the room's name. Google's
 * own Meet transcripts do exactly the same: in-room speech is attributed to the room device's robot
 * account, whose `displayName` is "the administrator-specified device name".
 *
 * This module does NOT separate those voices. It makes the existing, honest answer LEGIBLE: the
 * speaker is a room, and `speaker_kind` says so, so a downstream consumer stops reading a device
 * name as a person's name.
 *
 * THE TABLE IS DATA, NOT CODE. `DEFAULT_ROOM_PATTERNS` below mirrors
 * `core/meetings/contracts/room-identity/room-patterns.json` — the cross-language source of truth
 * shared with the Python read path. It is embedded rather than read from disk because the bot ships
 * as its own image and does not mount that directory; `room-identity.test.ts` reads the JSON and
 * fails if this table has drifted from it.
 *
 * WHY THE THREE VALUES ARE NOT SYMMETRIC:
 *   room    — EVIDENCE. A pattern matched.
 *   person  — A DEFAULT, not evidence. A resolved display name with no room marker.
 *   unknown — No name to classify (the binder refused, or the label is provisional).
 * A room kit named after a human — we have met a real one called `Steve Jobs` — reads as `person`
 * and always will, because nothing in the data distinguishes it. Anything that consumes this field
 * must treat `person` as "no room marker found", never as "confirmed human".
 */

/** transcript.v1 `#/$defs/SpeakerKind`. Additive and optional on every segment. */
export type SpeakerKind = 'room' | 'person' | 'unknown';

/**
 * The embedded mirror of `contracts/room-identity/room-patterns.json`. Each entry is
 * `[id, regexSource]`; every pattern is applied case-insensitively. Keep the ids and the sources
 * byte-identical to the JSON — the drift test compares them.
 */
export const DEFAULT_ROOM_PATTERNS: readonly (readonly [string, string])[] = [
  ['meet-device-resource', '(^|\\s)devices/'],
  ['room-word', '\\b(meeting\\s*room|conference\\s*room|board\\s*room|boardroom|huddle\\s*(room|space)|training\\s*room|war\\s*room|focus\\s*room|breakout\\s*room)\\b'],
  ['numbered-room', '\\b(room|rm)[\\s._-]*\\d{1,4}\\b'],
  ['room-suffix', '\\((meeting\\s*)?(room|conference\\s*room)\\)\\s*$'],
  ['teams-room', '\\b(microsoft\\s*teams\\s*rooms?|teams\\s*rooms?|\\bMTR\\b|surface\\s*hub)\\b'],
  ['zoom-room', '\\bzoom\\s*rooms?\\b'],
  ['google-meet-hardware', '\\b(google\\s*meet\\s*hardware|meet\\s*hardware|series\\s*one\\s*(desk|board)?|chromebase|chromebox)\\b'],
  ['vendor-room-kit', '\\b(rally\\s*bar|rallybar|logitech\\s*(rally|tap|roommate)|tap\\s*(ip|scheduler)|roommate|poly\\s*(studio|g7500|x30|x50|x52|x70)|neat\\s*(bar|board|frame|pad)|yealink\\s*(meetingbar|mvc|a\\d{2})|crestron\\s*(flex|uc-)|cisco\\s*(room\\s*(kit|bar|navigator)|webex\\s*room)|webex\\s*room\\s*kit)\\b'],
  ['room-word-non-english', '\\b(vergaderruimte|vergaderzaal|besprechungsraum|konferenzraum|sitzungszimmer|salle\\s*de\\s*r[eé]union|sala\\s*de\\s*reuni[oó]n|sala\\s*riunioni|sala\\s*de\\s*reuni[oõ]es|m[oö]tesrum|m[oø]terom|neuvotteluhuone)\\b'],
] as const;

/** The env var that replaces (or, with a leading `"+"`, extends) the table at runtime. */
export const ROOM_PATTERNS_ENV = 'VEXA_ROOM_PATTERNS';

/** A compiled classifier. `matched` names WHICH pattern fired, so a validator can say why. */
export interface RoomClassifier {
  (displayName: string | null | undefined): SpeakerKind;
  /** The pattern id that classified this name as a room, or null. Diagnostic — never a name. */
  matched(displayName: string | null | undefined): string | null;
  /** The pattern ids in force, in order. `env:<n>` for patterns supplied by the override. */
  readonly patternIds: readonly string[];
}

/**
 * Labels that name NOBODY. These are how a lane talks to itself about a refusal — the provisional
 * segmentation id, the word the lane publishes it as, the stable letter an unnamed transport track
 * carries, and the bot's blanked display string. A name in this set is `unknown`, never `person`:
 * claiming a person where the binder refused would be the same failed claim the blanking rule
 * exists to stop.
 */
const NO_NAME = /^(?:|seg_\d+|ch-\d+(?::\d+)?|Speaker|Speaker [A-Z]+|Unknown|unknown)$/;

/** Parse the env override. Returns null when unset, empty, or malformed — never throws, because a
 *  bad env var must not take a running bot off the air. `onWarn` surfaces the refusal. */
export function parseRoomPatternsEnv(
  raw: string | undefined,
  onWarn?: (msg: string) => void,
): { mode: 'replace' | 'append'; sources: string[] } | null {
  const text = (raw ?? '').trim();
  if (!text) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    onWarn?.(`[room-identity] ${ROOM_PATTERNS_ENV} is not valid JSON — ignored, defaults stand`);
    return null;
  }
  if (!Array.isArray(parsed) || parsed.some((p) => typeof p !== 'string')) {
    onWarn?.(`[room-identity] ${ROOM_PATTERNS_ENV} must be a JSON array of regex strings — ignored, defaults stand`);
    return null;
  }
  const list = parsed as string[];
  const mode = list[0] === '+' ? 'append' : 'replace';
  const sources = (mode === 'append' ? list.slice(1) : list).filter((s) => s.trim().length > 0);
  if (!sources.length) {
    onWarn?.(`[room-identity] ${ROOM_PATTERNS_ENV} carried no patterns — ignored, defaults stand`);
    return null;
  }
  return { mode, sources };
}

/**
 * Compile the classifier. With no override it is the default table; with a valid
 * `VEXA_ROOM_PATTERNS` it is that table (or the defaults plus it, in `+` mode). An individual
 * pattern that will not compile is DROPPED with a warning rather than failing the whole override —
 * one bad regex in an operator's list must not silently disable every other one.
 */
export function createRoomClassifier(opts: {
  env?: Record<string, string | undefined>;
  onWarn?: (msg: string) => void;
} = {}): RoomClassifier {
  const warn = opts.onWarn ?? ((m: string) => console.warn(m));
  const override = parseRoomPatternsEnv(opts.env?.[ROOM_PATTERNS_ENV], warn);

  const requested: [string, string][] =
    override === null
      ? DEFAULT_ROOM_PATTERNS.map(([id, src]) => [id, src])
      : override.mode === 'append'
        ? [
            ...DEFAULT_ROOM_PATTERNS.map(([id, src]) => [id, src] as [string, string]),
            ...override.sources.map((src, i) => [`env:${i}`, src] as [string, string]),
          ]
        : override.sources.map((src, i) => [`env:${i}`, src] as [string, string]);

  const compiled: { id: string; re: RegExp }[] = [];
  for (const [id, src] of requested) {
    try {
      compiled.push({ id, re: new RegExp(src, 'i') });
    } catch {
      warn(`[room-identity] pattern ${id} did not compile — dropped`);
    }
  }

  const matched = (displayName: string | null | undefined): string | null => {
    const name = (displayName ?? '').trim();
    if (!name || NO_NAME.test(name)) return null;
    for (const { id, re } of compiled) if (re.test(name)) return id;
    return null;
  };

  const classify = ((displayName: string | null | undefined): SpeakerKind => {
    const name = (displayName ?? '').trim();
    if (!name || NO_NAME.test(name)) return 'unknown';
    return matched(name) ? 'room' : 'person';
  }) as RoomClassifier;

  return Object.defineProperties(classify, {
    matched: { value: matched },
    patternIds: { value: compiled.map((c) => c.id) },
  }) as RoomClassifier;
}

/** The process-wide classifier, compiled once from `process.env`. The bot's transcript producer
 *  uses this; tests build their own with an explicit `env` so they never depend on the host's. */
let processClassifier: RoomClassifier | undefined;
export function roomClassifier(): RoomClassifier {
  if (!processClassifier) {
    processClassifier = createRoomClassifier({
      env: typeof process !== 'undefined' ? (process.env as Record<string, string | undefined>) : {},
    });
  }
  return processClassifier;
}

/** Convenience: classify one display name with the process classifier. */
export function speakerKind(displayName: string | null | undefined): SpeakerKind {
  return roomClassifier()(displayName);
}
