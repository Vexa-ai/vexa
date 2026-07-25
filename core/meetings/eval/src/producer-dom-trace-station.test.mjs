#!/usr/bin/env node
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { pageScript, sanitizeTrace } from './producer-dom-trace-station.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATION = path.join(HERE, 'producer-dom-trace-station.mjs');
const temporaryRoot = fs.mkdtempSync(
  path.join(os.tmpdir(), 'vexa-producer-dom-trace-test-'),
);

function run(args, input) {
  return spawnSync(process.execPath, [STATION, ...args], {
    input,
    encoding: 'utf8',
  });
}

function ndjson(records) {
  return `${records.map((record) => JSON.stringify(record)).join('\n')}\n`;
}

function runPageCollector(platform, document) {
  let interval = null;
  let now = 0;
  const messages = [];
  const window = {};
  const context = vm.createContext({
    window,
    document,
    performance: { now: () => now },
    setInterval: (callback) => {
      interval = callback;
      return 1;
    },
    clearInterval: () => {
      interval = null;
    },
    console: {
      info: (message) => messages.push(message),
      error: (message) => messages.push(message),
    },
  });
  vm.runInContext(pageScript(platform), context);
  return {
    messages,
    poll(atMs) {
      now = atMs;
      assert.ok(interval, 'collector interval is running');
      interval();
    },
    stop() {
      return window.__vexaProducerTrace.stop();
    },
  };
}

const teamsHeader = {
  record: 'header',
  schema: 'producer_dom_trace.v1',
  platform: 'teams',
  signal: 'dom-outline',
  provenance: 'captured',
  timebase: 'relative-ms',
};
const teamsRow = {
  record: 'tile-state',
  atMs: 0,
  tileId: 'tile-001',
  nameToken: 'UNRESOLVED',
  signalState: 'present',
  voiceState: 'speaking',
};
const zoomHeader = {
  record: 'header',
  schema: 'producer_dom_trace.v1',
  platform: 'zoom',
  signal: 'dom-active',
  provenance: 'captured',
  timebase: 'relative-ms',
  pollMs: 250,
  confirmPolls: 2,
};
const zoomRows = [
  { atMs: 0, view: 'none', footer: 'absent' },
  {
    atMs: 250,
    view: 'speaker-active',
    footer: 'named',
    participant: 'speaker-a',
  },
  { atMs: 500, view: 'speaker-active', footer: 'empty' },
  { atMs: 750, view: 'single-main-active', footer: 'read-fault' },
];

try {
  const help = run(['--help']);
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /already-pseudonymized NDJSON/);
  assert.match(help.stdout, /platform replay dialect byte-for-byte/);
  assert.match(help.stdout, /never replaced/);

  for (const platform of ['teams', 'zoom']) {
    const script = pageScript(platform);
    new Function(script);
    assert.match(script, /window\.__vexaProducerTrace/);
    assert.match(script, /provenance":"captured"/);
    assert.doesNotMatch(script, /ws:\/\/|wss:\/\/|https?:\/\//);
  }
  assert.match(pageScript('teams'), /record: "tile-state"/);
  assert.match(pageScript('teams'), /SPEAKER_A/);
  assert.match(pageScript('zoom'), /poll \* 250/);
  assert.match(pageScript('zoom'), /speaker-bar-active/);

  const secretTeamsName = { textContent: 'Private Teams Name' };
  const activeOutline = {
    classList: { contains: (name) => name === 'vdi-frame-occlusion' },
    parentElement: null,
  };
  const teamsTile = {
    parentElement: null,
    getAttribute: (key) => (
      key === 'data-participant-id' ? 'private-participant-id' : null
    ),
    querySelector: (selector) => {
      if (selector === '[data-tid="voice-level-stream-outline"]') return activeOutline;
      if (selector === 'div[class*="___2u340f0"]') return secretTeamsName;
      return null;
    },
  };
  activeOutline.parentElement = teamsTile;
  const teamsPage = runPageCollector('teams', {
    querySelectorAll: () => [teamsTile],
  });
  const teamsPageOutput = teamsPage.stop();
  assert.deepEqual(
    teamsPageOutput.trim().split('\n').map(JSON.parse),
    [
      teamsHeader,
      {
        record: 'tile-state',
        atMs: 0,
        tileId: 'tile-001',
        nameToken: 'SPEAKER_A',
        signalState: 'present',
        voiceState: 'speaking',
      },
    ],
  );
  assert.doesNotMatch(teamsPageOutput, /Private Teams Name|private-participant-id/);
  assert.ok(
    teamsPage.messages.some((message) => /mapping SPEAKER_A/.test(message)),
    'Teams collector asks for one in-page pseudonym attestation',
  );
  assert.doesNotMatch(teamsPage.messages.join('\n'), /Private Teams Name/);

  const controlOutline = {
    classList: { contains: (name) => name === 'vdi-frame-occlusion' },
    parentElement: null,
  };
  const teamsAriaControlTile = {
    parentElement: null,
    getAttribute: (key) => {
      if (key === 'data-participant-id') return 'private-control-participant';
      if (key === 'aria-label') return 'name: mic';
      return null;
    },
    querySelector: (selector) => (
      selector === '[data-tid="voice-level-stream-outline"]'
        ? controlOutline
        : null
    ),
  };
  controlOutline.parentElement = teamsAriaControlTile;
  const teamsAriaControlPage = runPageCollector('teams', {
    querySelectorAll: () => [teamsAriaControlTile],
  });
  const teamsAriaControlOutput = teamsAriaControlPage.stop();
  assert.deepEqual(
    teamsAriaControlOutput.trim().split('\n').map(JSON.parse),
    [
      teamsHeader,
      {
        record: 'tile-state',
        atMs: 0,
        tileId: 'tile-001',
        nameToken: 'UNRESOLVED',
        signalState: 'present',
        voiceState: 'speaking',
      },
    ],
    'Teams aria fallback applies the same exact control-token filter as production',
  );
  assert.doesNotMatch(
    teamsAriaControlOutput,
    /mic|private-control-participant/,
  );

  const menuOutline = {
    classList: { contains: (name) => name === 'vdi-frame-occlusion' },
    parentElement: null,
  };
  const menuTile = {
    parentElement: null,
    getAttribute: (key) => (
      key === 'data-participant-id' ? 'private-menu-participant' : null
    ),
    querySelector: (selector) => {
      if (selector === '[data-tid="voice-level-stream-outline"]') return menuOutline;
      if (selector === 'div[class*="___2u340f0"]') {
        return { textContent: 'Private Menu Speaker' };
      }
      return null;
    },
  };
  menuOutline.parentElement = menuTile;
  const menuPage = runPageCollector('teams', {
    querySelectorAll: (selector) => (
      selector === '[role="menuitem"]' ? [menuTile] : []
    ),
  });
  const menuOutput = menuPage.stop();
  assert.deepEqual(
    menuOutput.trim().split('\n').map(JSON.parse),
    [
      teamsHeader,
      {
        record: 'tile-state',
        atMs: 0,
        tileId: 'tile-001',
        nameToken: 'SPEAKER_A',
        signalState: 'present',
        voiceState: 'speaking',
      },
    ],
    'Teams capture mirrors the production menuitem participant surface',
  );
  assert.doesNotMatch(menuOutput, /Private Menu Speaker|private-menu-participant/);

  let zoomRawName = 'Private Zoom One';
  const zoomFooter = {
    get innerText() { throw new Error('innerText must short-circuit'); },
    querySelector: () => ({ get textContent() { return zoomRawName; } }),
  };
  const zoomContainer = {
    querySelector: (selector) => (
      selector === '.video-avatar__avatar-footer' ? zoomFooter : null
    ),
  };
  const zoomPage = runPageCollector('zoom', {
    querySelector: (selector) => (
      selector === '.speaker-active-container__video-frame' ? zoomContainer : null
    ),
  });
  zoomRawName = 'Private Zoom Two';
  zoomPage.poll(250);
  const zoomPageOutput = zoomPage.stop();
  assert.deepEqual(
    zoomPageOutput.trim().split('\n').map(JSON.parse),
    [
      zoomHeader,
      {
        atMs: 0,
        view: 'speaker-active',
        footer: 'named',
        participant: 'speaker-a',
      },
      {
        atMs: 250,
        view: 'speaker-active',
        footer: 'named',
        participant: 'speaker-b',
      },
    ],
  );
  assert.doesNotMatch(zoomPageOutput, /Private Zoom One|Private Zoom Two/);
  assert.ok(
    zoomPage.messages.some((message) => /mapping speaker-a/.test(message)),
    'Zoom collector asks for one in-page pseudonym attestation',
  );
  assert.doesNotMatch(zoomPage.messages.join('\n'), /Private Zoom One|Private Zoom Two/);

  const zoomQueryFaultPage = runPageCollector('zoom', {
    querySelector: () => {
      throw new Error('private selector fault');
    },
  });
  const zoomQueryFaultOutput = zoomQueryFaultPage.stop();
  assert.deepEqual(
    zoomQueryFaultOutput.trim().split('\n').map(JSON.parse),
    [
      zoomHeader,
      { atMs: 0, view: 'speaker-active', footer: 'read-fault' },
    ],
    'top-level active-view reads preserve the production read-fault observation',
  );
  assert.doesNotMatch(zoomQueryFaultOutput, /private selector fault/);

  const zoomFallbackFooter = {
    innerText: 'Private Zoom Fallback',
    querySelector: () => ({ textContent: '   ' }),
  };
  const zoomFallbackContainer = {
    querySelector: (selector) => (
      selector === '.video-avatar__avatar-footer' ? zoomFallbackFooter : null
    ),
  };
  const zoomFallbackPage = runPageCollector('zoom', {
    querySelector: (selector) => (
      selector === '.speaker-active-container__video-frame'
        ? zoomFallbackContainer
        : null
    ),
  });
  const zoomFallbackOutput = zoomFallbackPage.stop();
  assert.deepEqual(
    zoomFallbackOutput.trim().split('\n').map(JSON.parse),
    [
      zoomHeader,
      {
        atMs: 0,
        view: 'speaker-active',
        footer: 'named',
        participant: 'speaker-a',
      },
    ],
    'whitespace span text falls back to the canonical footer text path',
  );
  assert.doesNotMatch(zoomFallbackOutput, /Private Zoom Fallback/);

  const teamsInput = ndjson([
    teamsHeader,
    teamsRow,
    {
      ...teamsRow,
      atMs: 300,
      nameToken: 'SPEAKER_A',
      voiceState: 'silent',
    },
  ]);
  const teamsOut = path.join(temporaryRoot, 'teams.jsonl');
  const teams = run([
    'sanitize',
    '--platform',
    'teams',
    '--input',
    '-',
    '--out',
    teamsOut,
  ], teamsInput);
  assert.equal(teams.status, 0, teams.stderr);
  assert.equal(fs.readFileSync(teamsOut, 'utf8'), teamsInput);
  assert.match(teams.stderr, /provenance=captured/);
  assert.equal(fs.statSync(teamsOut).mode & 0o777, 0o600);

  const zoomInput = ndjson([zoomHeader, ...zoomRows]);
  const zoom = run([
    'sanitize',
    '--platform',
    'zoom',
    '--input',
    '-',
    '--out',
    '-',
  ], zoomInput);
  assert.equal(zoom.status, 0, zoom.stderr);
  assert.equal(zoom.stdout, zoomInput);

  const rawLeak = run([
    'sanitize',
    '--platform',
    'teams',
    '--input',
    '-',
    '--out',
    '-',
  ], ndjson([
    teamsHeader,
    { ...teamsRow, displayName: 'must-never-cross' },
  ]));
  assert.notEqual(rawLeak.status, 0);
  assert.match(rawLeak.stderr, /key set/);
  assert.doesNotMatch(rawLeak.stderr, /must-never-cross/);

  const duplicateTeamsIdentity = run([
    'sanitize',
    '--platform',
    'teams',
    '--input',
    '-',
    '--out',
    '-',
  ], `${JSON.stringify(teamsHeader)}\n`
    + '{"record":"tile-state","atMs":0,"tileId":"tile-001",'
    + '"nameToken":"must-never-cross","nameToken":"SPEAKER_A",'
    + '"signalState":"present","voiceState":"speaking"}\n');
  assert.notEqual(duplicateTeamsIdentity.status, 0);
  assert.match(duplicateTeamsIdentity.stderr, /duplicate-key Teams row/);
  assert.doesNotMatch(duplicateTeamsIdentity.stderr, /must-never-cross/);

  const duplicateZoomProvenance = run([
    'sanitize',
    '--platform',
    'zoom',
    '--input',
    '-',
    '--out',
    '-',
  ], `${JSON.stringify(zoomHeader).replace(
    '"provenance":"captured"',
    '"provenance":"authored","provenance":"captured"',
  )}\n${JSON.stringify(zoomRows[0])}\n`);
  assert.notEqual(duplicateZoomProvenance.status, 0);
  assert.match(duplicateZoomProvenance.stderr, /duplicate-key header/);

  const authoredRelabel = run([
    'sanitize',
    '--platform',
    'teams',
    '--input',
    '-',
    '--out',
    '-',
  ], ndjson([
    { ...teamsHeader, provenance: 'authored' },
    teamsRow,
  ]));
  assert.notEqual(authoredRelabel.status, 0);
  assert.match(authoredRelabel.stderr, /captured producer header/);

  const freeFormName = run([
    'sanitize',
    '--platform',
    'teams',
    '--input',
    '-',
    '--out',
    '-',
  ], ndjson([
    teamsHeader,
    { ...teamsRow, nameToken: 'A Real Name' },
  ]));
  assert.notEqual(freeFormName.status, 0);
  assert.match(freeFormName.stderr, /tile-state contract/);
  assert.doesNotMatch(freeFormName.stderr, /A Real Name/);

  const wrongPlatformContract = run([
    'sanitize',
    '--platform',
    'zoom',
    '--input',
    '-',
    '--out',
    '-',
  ], ndjson([
    zoomHeader,
    { ...teamsRow, record: undefined },
  ]));
  assert.notEqual(wrongPlatformContract.status, 0);
  assert.match(wrongPlatformContract.stderr, /Zoom poll key set/);

  const offCadence = run([
    'sanitize',
    '--platform',
    'zoom',
    '--input',
    '-',
    '--out',
    '-',
  ], ndjson([
    zoomHeader,
    zoomRows[0],
    { ...zoomRows[1], atMs: 251 },
  ]));
  assert.notEqual(offCadence.status, 0);
  assert.match(offCadence.stderr, /Zoom poll contract/);

  const invalidNamedState = run([
    'sanitize',
    '--platform',
    'zoom',
    '--input',
    '-',
    '--out',
    '-',
  ], ndjson([
    zoomHeader,
    {
      atMs: 0,
      view: 'none',
      footer: 'named',
      participant: 'speaker-a',
    },
  ]));
  assert.notEqual(invalidNamedState.status, 0);
  assert.match(invalidNamedState.stderr, /Zoom poll contract/);

  const maximumTeamsRows = Array.from({ length: 4095 }, (_, index) => ({
    ...teamsRow,
    atMs: index,
    tileId: `tile-${String((index % 64) + 1).padStart(3, '0')}`,
  }));
  const maximumTeams = ndjson([teamsHeader, ...maximumTeamsRows]);
  assert.equal(
    sanitizeTrace(maximumTeams, 'teams'),
    maximumTeams,
    'the literal 4096-record/64-tile Teams boundary is admitted',
  );
  assert.throws(
    () => sanitizeTrace(
      ndjson([teamsHeader, ...maximumTeamsRows, teamsRow]),
      'teams',
    ),
    /record limit/,
  );
  assert.throws(
    () => sanitizeTrace(ndjson([
      teamsHeader,
      ...Array.from({ length: 65 }, (_, index) => ({
        ...teamsRow,
        tileId: `tile-${String(index + 1).padStart(3, '0')}`,
      })),
    ]), 'teams'),
    /tile limit/,
  );

  const maximumZoomRows = Array.from({ length: 10_000 }, (_, index) => ({
    atMs: index * 250,
    view: 'none',
    footer: 'absent',
  }));
  const maximumZoom = ndjson([zoomHeader, ...maximumZoomRows]);
  assert.equal(
    sanitizeTrace(maximumZoom, 'zoom'),
    maximumZoom,
    'the literal 10000-poll Zoom boundary is admitted',
  );
  assert.throws(
    () => sanitizeTrace(
      ndjson([
        zoomHeader,
        ...maximumZoomRows,
        { atMs: 2_500_000, view: 'none', footer: 'absent' },
      ]),
      'zoom',
    ),
    /record limit/,
  );

  const overwrite = run([
    'sanitize',
    '--platform',
    'teams',
    '--input',
    '-',
    '--out',
    teamsOut,
  ], teamsInput);
  assert.notEqual(overwrite.status, 0);
  assert.match(overwrite.stderr, /EEXIST/);
  assert.equal(fs.readFileSync(teamsOut, 'utf8'), teamsInput);

  console.log(
    'producer-dom-trace-station: 23 checks passed '
      + '(help, page syntax, in-page Teams/Zoom pseudonymization/attestation, byte dialects, '
      + 'Teams/Zoom read parity, raw/duplicate leak, provenance, free-form identity, producer split, '
      + 'cadence/state, literal platform bounds, no-overwrite)',
  );
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
}
