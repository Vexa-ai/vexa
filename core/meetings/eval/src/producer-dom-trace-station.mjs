#!/usr/bin/env node
/**
 * Local, human-admitted producer DOM trace capture for Teams and Zoom.
 *
 * `page-script` prints a collector for the DevTools console of an already
 * admitted web meeting. Raw names and DOM identifiers are mapped to the fixed
 * producer_dom_trace.v1 vocabulary inside that page. `sanitize` then applies
 * the same closed keys, enums, time, and size limits as the platform-local
 * replay parsers before host bytes are written.
 */
import fs from 'node:fs';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const SCHEMA = 'producer_dom_trace.v1';
const TEAM_NAME_TOKENS = new Set([
  'SPEAKER_A',
  'SPEAKER_B',
  'SPEAKER_C',
  'UNRESOLVED',
]);
const ZOOM_VIEWS = new Set([
  'none',
  'speaker-active',
  'speaker-bar-active',
  'single-main-active',
]);
const ZOOM_FOOTERS = new Set(['absent', 'empty', 'named', 'read-fault']);
const ZOOM_PARTICIPANTS = new Set(['speaker-a', 'speaker-b', 'speaker-c']);
const TEAM_MAX_RECORDS = 4096;
const TEAM_MAX_TILES = 64;
const TEAM_MAX_AT_MS = 10 * 60 * 1000;
const ZOOM_MAX_ROWS = 10_000;
const ZOOM_POLL_MS = 250;
const ZOOM_CONFIRM_POLLS = 2;
const TEAM_TILE_ID = /^tile-[0-9]{3}$/;

const TEAM_HEADER_KEYS = [
  'record',
  'schema',
  'platform',
  'signal',
  'provenance',
  'timebase',
];
const TEAM_ROW_KEYS = [
  'record',
  'atMs',
  'tileId',
  'nameToken',
  'signalState',
  'voiceState',
];
const ZOOM_HEADER_KEYS = [
  'record',
  'schema',
  'platform',
  'signal',
  'provenance',
  'timebase',
  'pollMs',
  'confirmPolls',
];
const ZOOM_ROW_BASE_KEYS = ['atMs', 'view', 'footer'];

function fail(message) {
  throw new Error(message);
}

function hasExactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function parseLines(text, maximumRecords) {
  if (typeof text !== 'string' || text.length === 0) fail('trace is empty');
  if (text.startsWith('\uFEFF')) fail('byte-order marks are not allowed');
  const lines = text.split(/\r?\n/);
  if (lines.at(-1) === '') lines.pop();
  if (lines.length < 2) fail('trace requires a header and at least one row');
  if (lines.length > maximumRecords) fail('trace exceeds the platform record limit');
  if (lines.some((line) => line.trim() === '')) fail('blank NDJSON records are forbidden');
  const records = lines.map((line, index) => {
    try {
      return JSON.parse(line);
    } catch {
      // Rejected bytes may contain precisely the private DOM data this seam
      // keeps off the host, so errors never echo their input.
      fail(`line ${index + 1}: invalid JSON`);
    }
  });
  return { lines, records };
}

function validateCapturedHeader(value, platform, expectedKeys) {
  if (!hasExactKeys(value, expectedKeys)) fail('line 1: invalid header key set');
  if (
    value.record !== 'header'
    || value.schema !== SCHEMA
    || value.platform !== platform
    || value.signal !== (platform === 'teams' ? 'dom-outline' : 'dom-active')
    || value.provenance !== 'captured'
    || value.timebase !== 'relative-ms'
  ) {
    fail('line 1: invalid captured producer header');
  }
}

function sanitizeTeams(text) {
  const { lines, records } = parseLines(text, TEAM_MAX_RECORDS);
  validateCapturedHeader(records[0], 'teams', TEAM_HEADER_KEYS);
  const canonical = [{
    record: 'header',
    schema: SCHEMA,
    platform: 'teams',
    signal: 'dom-outline',
    provenance: 'captured',
    timebase: 'relative-ms',
  }];
  if (lines[0] !== JSON.stringify(canonical[0])) {
    fail('line 1: noncanonical or duplicate-key header');
  }
  const tiles = new Set();
  let priorAtMs = -1;
  for (let index = 1; index < records.length; index += 1) {
    const value = records[index];
    if (!hasExactKeys(value, TEAM_ROW_KEYS)) {
      fail(`line ${index + 1}: invalid Teams tile-state key set`);
    }
    if (
      value.record !== 'tile-state'
      || !Number.isSafeInteger(value.atMs)
      || value.atMs < 0
      || value.atMs > TEAM_MAX_AT_MS
      || value.atMs < priorAtMs
      || typeof value.tileId !== 'string'
      || !TEAM_TILE_ID.test(value.tileId)
      || !TEAM_NAME_TOKENS.has(value.nameToken)
      || !new Set(['present', 'absent']).has(value.signalState)
      || !new Set(['speaking', 'silent']).has(value.voiceState)
    ) {
      fail(`line ${index + 1}: invalid Teams tile-state contract`);
    }
    if (!tiles.has(value.tileId)) {
      if (tiles.size >= TEAM_MAX_TILES) {
        fail(`line ${index + 1}: Teams tile limit exceeded`);
      }
      const expectedTileId = `tile-${String(tiles.size + 1).padStart(3, '0')}`;
      if (value.tileId !== expectedTileId) {
        fail(`line ${index + 1}: Teams tile IDs must be sequential ordinals`);
      }
      tiles.add(value.tileId);
    }
    priorAtMs = value.atMs;
    const canonicalRow = {
      record: 'tile-state',
      atMs: value.atMs,
      tileId: value.tileId,
      nameToken: value.nameToken,
      signalState: value.signalState,
      voiceState: value.voiceState,
    };
    if (lines[index] !== JSON.stringify(canonicalRow)) {
      fail(`line ${index + 1}: noncanonical or duplicate-key Teams row`);
    }
    canonical.push(canonicalRow);
  }
  return `${canonical.map((record) => JSON.stringify(record)).join('\n')}\n`;
}

function sanitizeZoom(text) {
  const { lines, records } = parseLines(text, ZOOM_MAX_ROWS + 1);
  validateCapturedHeader(records[0], 'zoom', ZOOM_HEADER_KEYS);
  if (
    records[0].pollMs !== ZOOM_POLL_MS
    || records[0].confirmPolls !== ZOOM_CONFIRM_POLLS
  ) {
    fail('line 1: invalid Zoom poll contract');
  }
  const canonical = [{
    record: 'header',
    schema: SCHEMA,
    platform: 'zoom',
    signal: 'dom-active',
    provenance: 'captured',
    timebase: 'relative-ms',
    pollMs: ZOOM_POLL_MS,
    confirmPolls: ZOOM_CONFIRM_POLLS,
  }];
  if (lines[0] !== JSON.stringify(canonical[0])) {
    fail('line 1: noncanonical or duplicate-key header');
  }
  let priorAtMs = null;
  for (let index = 1; index < records.length; index += 1) {
    const value = records[index];
    const named = value?.footer === 'named';
    const expectedKeys = named
      ? [...ZOOM_ROW_BASE_KEYS, 'participant']
      : ZOOM_ROW_BASE_KEYS;
    if (!hasExactKeys(value, expectedKeys)) {
      fail(`line ${index + 1}: invalid Zoom poll key set`);
    }
    if (
      !Number.isSafeInteger(value.atMs)
      || value.atMs < 0
      || (priorAtMs === null ? value.atMs !== 0 : value.atMs - priorAtMs !== ZOOM_POLL_MS)
      || !ZOOM_VIEWS.has(value.view)
      || !ZOOM_FOOTERS.has(value.footer)
      || (value.view === 'none' && value.footer !== 'absent')
      || (named && (value.view === 'none' || !ZOOM_PARTICIPANTS.has(value.participant)))
      || (!named && Object.hasOwn(value, 'participant'))
    ) {
      fail(`line ${index + 1}: invalid Zoom poll contract`);
    }
    priorAtMs = value.atMs;
    const canonicalRow = named
      ? {
          atMs: value.atMs,
          view: value.view,
          footer: 'named',
          participant: value.participant,
        }
      : {
          atMs: value.atMs,
          view: value.view,
          footer: value.footer,
        };
    if (lines[index] !== JSON.stringify(canonicalRow)) {
      fail(`line ${index + 1}: noncanonical or duplicate-key Zoom row`);
    }
    canonical.push(canonicalRow);
  }
  return `${canonical.map((record) => JSON.stringify(record)).join('\n')}\n`;
}

export function sanitizeTrace(text, platform) {
  if (platform === 'teams') return sanitizeTeams(text);
  if (platform === 'zoom') return sanitizeZoom(text);
  fail('--platform must be teams or zoom');
}

function pagePrelude(header) {
  return `(() => {
  "use strict";
  if (window.__vexaProducerTrace?.running) {
    throw new Error("producer_dom_trace.v1 collector is already running");
  }
  const rows = [${JSON.stringify(header)}];
  let timer = null;
  let running = true;
  let failure = null;
  const rawNames = new Map();
  const elementTiles = new WeakMap();
  const rawTiles = new Map();
  const halt = (code) => {
    failure = code;
    running = false;
    if (timer !== null) clearInterval(timer);
    rawNames.clear();
    rawTiles.clear();
    console.error(\`producer_dom_trace.v1 capture halted: \${code}\`);
  };
`;
}

function pageEpilogue(pollMs) {
  return `  sample();
  if (running) timer = setInterval(sample, ${pollMs});
  window.__vexaProducerTrace = {
    get running() { return running; },
    stop() {
      if (timer !== null) clearInterval(timer);
      running = false;
      if (failure) throw new Error(\`producer_dom_trace.v1 capture failed: \${failure}\`);
      if (rows.length < 2) throw new Error("producer_dom_trace.v1 captured no observations");
      const output = rows.map((record) => JSON.stringify(record)).join("\\n") + "\\n";
      rawNames.clear();
      rawTiles.clear();
      window.__vexaProducerTrace = { running: false };
      return output;
    }
  };
  console.info("producer_dom_trace.v1 ready; call copy(window.__vexaProducerTrace.stop()) when finished");
})();`;
}

function teamsPageScript() {
  const header = {
    record: 'header',
    schema: SCHEMA,
    platform: 'teams',
    signal: 'dom-outline',
    provenance: 'captured',
    timebase: 'relative-ms',
  };
  return `${pagePrelude(header)}  const started = performance.now();
  const PARTICIPANT_SELECTORS = [
    '[data-tid*="participant"]',
    '[aria-label*="participant"]',
    '[data-tid*="roster"]',
    '[data-tid*="roster-item"]',
    '[data-tid*="video-tile"]',
    '[data-tid*="videoTile"]',
    '[data-tid*="participant-tile"]',
    '[data-tid*="participantTile"]',
    '[role="listitem"]',
    '[role="menuitem"]',
    '.participant-tile',
    '.video-tile',
    '.roster-item'
  ];
  const NAME_SELECTORS = [
    'div[class*="___2u340f0"]',
    '[data-tid*="display-name"]',
    '[data-tid*="participant-name"]',
    '[data-tid*="user-name"]',
    '[aria-label*="name"]',
    '.participant-name',
    '.display-name',
    '.user-name',
    '.roster-item-name',
    '.video-tile-name',
    'span[title]'
  ];
  const CONTROL_LABELS = new Set([
    "more vert", "mic off", "mic", "videocam", "videocam off",
    "present to all", "devices", "speaker", "speakers", "microphone",
    "camera", "camera off", "share", "chat", "participant", "user",
    "mute", "unmute"
  ]);
  const TIMER_LABEL = /^(?:\\d{1,2}:)?\\d{1,2}:\\d{2}$/;
  const NAME_TOKENS = ["SPEAKER_A", "SPEAKER_B", "SPEAKER_C"];
  const previous = new Map();
  let tileSequence = 0;
  let nameSequence = 0;
  const nameCandidate = (raw) => {
    const candidate = raw.trim();
    if (candidate.length <= 1 || candidate.length >= 50) return "";
    const normalized = candidate.toLowerCase().replace(/[_\\s-]+/g, " ");
    return CONTROL_LABELS.has(normalized) || TIMER_LABEL.test(normalized)
      ? ""
      : candidate;
  };
  const tileFor = (raw, element) => {
    if (raw && rawTiles.has(raw)) return rawTiles.get(raw);
    if (!raw && elementTiles.has(element)) return elementTiles.get(element);
    tileSequence += 1;
    if (tileSequence > ${TEAM_MAX_TILES}) {
      halt("tile-limit");
      return null;
    }
    const tile = \`tile-\${String(tileSequence).padStart(3, "0")}\`;
    if (raw) rawTiles.set(raw, tile);
    else elementTiles.set(element, tile);
    return tile;
  };
  const nameTokenFor = (raw, element) => {
    if (!raw) return "UNRESOLVED";
    if (rawNames.has(raw)) return rawNames.get(raw);
    if (nameSequence >= NAME_TOKENS.length) {
      halt("name-token-limit");
      return null;
    }
    const token = NAME_TOKENS[nameSequence++];
    rawNames.set(raw, token);
    console.info(
      \`producer_dom_trace.v1 mapping \${token}: visually attest the attached tile\`,
      element
    );
    return token;
  };
  const stableRawId = (element) => {
    const direct = element.getAttribute("data-acc-element-id")
      || element.getAttribute("data-tid")
      || element.getAttribute("data-participant-id")
      || element.getAttribute("data-user-id")
      || element.getAttribute("data-object-id")
      || element.getAttribute("id");
    if (direct) return direct;
    const child = element.querySelector(
      '[data-tid], [data-participant-id], [data-user-id]'
    );
    return child
      ? child.getAttribute("data-tid")
        || child.getAttribute("data-participant-id")
        || child.getAttribute("data-user-id")
        || ""
      : "";
  };
  const rawName = (element) => {
    for (const selector of NAME_SELECTORS) {
      const node = element.querySelector(selector);
      if (!node) continue;
      const raw = (
        node.textContent
        || node.innerText
        || node.getAttribute("title")
        || node.getAttribute("aria-label")
        || ""
      ).trim();
      if (!raw) continue;
      const candidate = nameCandidate(raw);
      if (candidate) return candidate;
    }
    const aria = element.getAttribute("aria-label") || "";
    const match = aria.includes("name") ? aria.match(/name[:\\s]+([^,]+)/i) : null;
    return nameCandidate(match?.[1] || "");
  };
  const append = (state, atMs) => {
    if (rows.length >= ${TEAM_MAX_RECORDS}) {
      halt("record-limit");
      return;
    }
    rows.push({
      record: "tile-state",
      atMs,
      tileId: state.tileId,
      nameToken: state.nameToken,
      signalState: state.signalState,
      voiceState: state.voiceState
    });
  };
  const sample = () => {
    if (!running) return;
    const atMs = Math.max(0, Math.round(performance.now() - started));
    if (atMs > ${TEAM_MAX_AT_MS}) {
      running = false;
      if (timer !== null) clearInterval(timer);
      console.info("producer_dom_trace.v1 capture reached the 10-minute limit");
      return;
    }
    const elements = new Set();
    for (const selector of PARTICIPANT_SELECTORS) {
      for (const element of document.querySelectorAll(selector)) elements.add(element);
    }
    const seen = new Set();
    for (const element of elements) {
      const tileId = tileFor(stableRawId(element), element);
      if (!running || !tileId || seen.has(tileId)) continue;
      seen.add(tileId);
      const nameToken = nameTokenFor(rawName(element), element);
      if (!running || !nameToken) return;
      const outline = element.querySelector('[data-tid="voice-level-stream-outline"]');
      let cursor = outline;
      let speaking = false;
      while (cursor) {
        if (cursor.classList?.contains("vdi-frame-occlusion")) {
          speaking = true;
          break;
        }
        cursor = cursor.parentElement;
      }
      const state = {
        tileId,
        nameToken,
        signalState: outline ? "present" : "absent",
        voiceState: outline && speaking ? "speaking" : "silent"
      };
      const key = [
        state.nameToken,
        state.signalState,
        state.voiceState
      ].join("|");
      if (previous.get(tileId)?.key !== key) {
        append(state, atMs);
        previous.set(tileId, { ...state, key });
      }
    }
    for (const [tileId, before] of previous) {
      if (seen.has(tileId) || before.signalState === "absent") continue;
      const state = {
        tileId,
        nameToken: before.nameToken,
        signalState: "absent",
        voiceState: "silent"
      };
      append(state, atMs);
      previous.set(tileId, { ...state, key: \`\${state.nameToken}|absent|silent\` });
    }
  };
${pageEpilogue(200)}
`;
}

function zoomPageScript() {
  const header = {
    record: 'header',
    schema: SCHEMA,
    platform: 'zoom',
    signal: 'dom-active',
    provenance: 'captured',
    timebase: 'relative-ms',
    pollMs: ZOOM_POLL_MS,
    confirmPolls: ZOOM_CONFIRM_POLLS,
  };
  return `${pagePrelude(header)}  const ACTIVE_VIEWS = [
    ["speaker-active", ".speaker-active-container__video-frame"],
    ["speaker-bar-active", ".speaker-bar-container__video-frame--active"],
    ["single-main-active", ".single-main-container__video-frame"]
  ];
  const PARTICIPANTS = ["speaker-a", "speaker-b", "speaker-c"];
  let participantSequence = 0;
  let poll = 0;
  const participantFor = (raw, element) => {
    if (rawNames.has(raw)) return rawNames.get(raw);
    if (participantSequence >= PARTICIPANTS.length) {
      halt("participant-token-limit");
      return null;
    }
    const participant = PARTICIPANTS[participantSequence++];
    rawNames.set(raw, participant);
    console.info(
      \`producer_dom_trace.v1 mapping \${participant}: visually attest the attached tile\`,
      element
    );
    return participant;
  };
  const read = () => {
    let unresolved = null;
    for (const [view, selector] of ACTIVE_VIEWS) {
      let container;
      try {
        container = document.querySelector(selector);
      } catch {
        return { view, footer: "read-fault" };
      }
      if (!container) continue;
      try {
        const footer = container.querySelector(".video-avatar__avatar-footer");
        if (!footer) {
          unresolved ||= { view, footer: "absent" };
          continue;
        }
        const spanText = footer.querySelector("span")?.textContent?.trim() || "";
        const raw = (
          spanText
          || footer.innerText?.trim()
          || ""
        ).replace(/\\s+/g, " ").trim();
        if (!raw) {
          unresolved ||= { view, footer: "empty" };
          continue;
        }
        const participant = participantFor(raw, container);
        return participant ? { view, footer: "named", participant } : null;
      } catch {
        return { view, footer: "read-fault" };
      }
    }
    return unresolved || { view: "none", footer: "absent" };
  };
  const sample = () => {
    if (!running) return;
    if (poll >= ${ZOOM_MAX_ROWS}) {
      running = false;
      if (timer !== null) clearInterval(timer);
      console.info("producer_dom_trace.v1 capture reached the 10000-poll limit");
      return;
    }
    const state = read();
    if (!running || !state) return;
    rows.push({ atMs: poll * ${ZOOM_POLL_MS}, ...state });
    poll += 1;
  };
${pageEpilogue(ZOOM_POLL_MS)}
`;
}

export function pageScript(platform) {
  if (platform === 'teams') return teamsPageScript();
  if (platform === 'zoom') return zoomPageScript();
  fail('--platform must be teams or zoom');
}

function usage() {
  return `producer-dom-trace-station — local human-admitted Teams/Zoom capture

Usage:
  node src/producer-dom-trace-station.mjs page-script --platform teams|zoom
  node src/producer-dom-trace-station.mjs sanitize --platform teams|zoom \\
    --input <pseudonymized-page-export.ndjson|-> \\
    --out <producer-dom-trace.jsonl|->

🧑 HUMAN — browser admission/capture (stop and wait):
  1. Join and admit the Teams or Zoom WEB meeting yourself. This tool never
     launches, joins, authenticates, admits, or contacts a meeting.
  2. Print and paste the page collector into that admitted tab's DevTools:
       node src/producer-dom-trace-station.mjs page-script --platform teams
  3. Exercise exactly the intended speaker/name transition.
  4. When a new SPEAKER_/speaker- token is announced, glance at the attached
     tile and attest its displayed authored-test identity. Do not paste a
     customer identity into the host artifact.
  5. In the same Console run:
       copy(window.__vexaProducerTrace.stop())
  6. Paste that already-pseudonymized NDJSON into a new local file. Only then
     run sanitize. Use a new --out path; existing artifacts are never replaced.

Exact prompt:
  "Join the intended Teams or Zoom web meeting in Chrome and complete any
   admission yourself. In that admitted meeting tab, paste the generated
   collector into DevTools Console, exercise exactly the intended speaker/name
   transition, and visually attest each announced pseudonym against its attached
   authored-test tile. Then run copy(window.__vexaProducerTrace.stop()). Paste
   only that already-pseudonymized NDJSON into a new local file. Tell me when it
   is saved."

Output is the platform replay dialect byte-for-byte with provenance=captured.
Teams contains only tile-state enums and SPEAKER_A/B/C tokens; Zoom contains
only 250ms canonical view/footer polls and speaker-a/b/c tokens. Raw names,
DOM text/IDs/classes/aria/title, URLs, meeting IDs, and auth data cannot cross.
`;
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const values = {};
  for (let index = 0; index < rest.length; index += 1) {
    const token = rest[index];
    if (token === '--help' || token === '-h') values.help = true;
    else if (token.startsWith('--')) {
      const key = token.slice(2);
      const value = rest[index + 1];
      if (value === undefined || value.startsWith('--')) fail(`missing value for --${key}`);
      if (Object.hasOwn(values, key)) fail(`duplicate --${key}`);
      values[key] = value;
      index += 1;
    } else {
      fail('unexpected positional argument');
    }
  }
  return { command, values };
}

function readInput(inputPath) {
  return inputPath === '-' ? fs.readFileSync(0, 'utf8') : fs.readFileSync(inputPath, 'utf8');
}

function writeOutput(outputPath, text) {
  if (outputPath === '-') process.stdout.write(text);
  else fs.writeFileSync(outputPath, text, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
}

export function main(argv = process.argv.slice(2)) {
  const { command, values } = parseArgs(argv);
  if (values.help || command === '--help' || command === '-h' || !command) {
    process.stdout.write(usage());
    return 0;
  }
  if (command === 'page-script') {
    if (!values.platform || values.input || values.out) fail('invalid page-script arguments');
    process.stdout.write(`${pageScript(values.platform)}\n`);
    return 0;
  }
  if (command === 'sanitize') {
    if (!values.platform || !values.input || !values.out) {
      fail('sanitize requires --platform, --input, and --out');
    }
    const canonical = sanitizeTrace(readInput(values.input), values.platform);
    writeOutput(values.out, canonical);
    process.stderr.write(
      `producer_dom_trace.v1 validated: platform=${values.platform} provenance=captured\n`,
    );
    return 0;
  }
  fail('unknown command; use --help');
}

const isMain = process.argv[1]
  && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1]);
if (isMain) {
  try {
    process.exitCode = main();
  } catch (error) {
    const message = error instanceof Error ? error.message : 'unknown failure';
    process.stderr.write(`producer-dom-trace-station: ${message}\n`);
    process.exitCode = 1;
  }
}
