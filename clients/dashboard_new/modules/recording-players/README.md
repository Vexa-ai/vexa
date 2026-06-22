# @vexa/dash-recording-players — recording players (view brick)

_dashboard_new/ · module · presentational React players for a meeting recording._

Two **presentational** React components. Props in, DOM out: **no store, no fetch, no websocket** — the
caller injects the media URL(s). Clean modular rebuild of the vendored
`dashboard/src/components/recording/{audio-player,video-player}.tsx` (same behaviour, none of the
shadcn/`cn`/lucide coupling — self-contained markup + inline styles so it is drop-in renderable). Typed
by the recording fields [`@vexa/dash-contracts`](../dash-contracts/) exposes (`RecordingMaster.raw_url`
→ `src`, `RecordingMaster.duration_seconds` → fragment `duration`).

Front door: [`src/index.ts`](src/index.ts) — exports `AudioPlayer`, `VideoPlayer` (+ their prop / handle
types).

## Props contract

### `AudioPlayer` — `forwardRef<AudioPlayerHandle, AudioPlayerProps>`
| prop | type | meaning |
| --- | --- | --- |
| `src?` | `string` | single-recording source URL |
| `fragments?` | `AudioFragment[]` | ordered fragments for a multi-recording meeting (takes precedence over `src`); played sequentially, durations form one **stitched virtual timeline** |
| `onTimeUpdate?` | `(currentTime: number) => void` | fired with the virtual (stitched) current time, seconds |
| `onFragmentChange?` | `(fragmentIndex: number) => void` | fired when a new fragment becomes current |
| `compact?` | `boolean` | denser layout |
| `className?` | `string` | passthrough |

`AudioFragment = { src: string; duration: number; sessionUid?: string; createdAt?: string }`
(`src` = `RecordingMaster.raw_url`, `duration` = `RecordingMaster.duration_seconds`; `0` = unknown until
metadata loads). Ref handle: `seekTo(virtualSeconds)` and `seekToFragment(index, secondsInFragment)`.

Renders an `<audio preload="metadata">` plus Play/Pause, current-time, a seek scrubber, total duration,
a mute toggle, and (multi-fragment only) a `N/M` fragment indicator. Auto-advances to the next fragment
on `ended`.

### `VideoPlayer` — `forwardRef<VideoPlayerHandle, VideoPlayerProps>`
| prop | type | meaning |
| --- | --- | --- |
| `src` | `string` | video source URL (required) |
| `onTimeUpdate?` | `(currentTime: number) => void` | fired with the current time, seconds |
| `className?` | `string` | passthrough |

Ref handle: `seekTo(seconds)` (seek + play — used by a transcript to jump the playhead). Renders a
`<video preload="metadata">` plus an overlaid Play/Pause, mute, time label, fullscreen, and an error
overlay if the source fails.

`react` / `react-dom` are **peer** deps — the host app provides them.

## Verify

`tsc` clean: `pnpm --filter @vexa/dash-recording-players run build` (`tsconfig` adds the `DOM` lib +
`react-jsx`).

**L4 bulletproof gate** (`pnpm --filter @vexa/dash-recording-players test`): a **real chromium**
(Playwright) loads [`e2e/fixtures/players-render.html`](e2e/fixtures/players-render.html), which mounts
the **real** `AudioPlayer` + `VideoPlayer` (esbuild-bundled from this brick's source) over golden props,
and [`e2e/players-render.spec.ts`](e2e/players-render.spec.ts) asserts the **rendered DOM**: the
`<audio>`/`<video>` elements carry the golden `src`, a Play control + seek bar exist, the multi-fragment
player shows `1/2` and a `0:30` stitched total, and clicking Play flips the control to Pause. Green here
⇒ green for a human's browser. `globalSetup` re-bundles from source before every run, so the page always
exercises the current components. Graduates to the real stack by swapping the golden data: URLs for real
`RecordingMaster.raw_url`s — the DOM assertions are unchanged.

Run once if chromium is missing: `pnpm --filter @vexa/dash-recording-players run install:browser`.
