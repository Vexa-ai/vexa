# meet-join — agent debug brief

You are debugging **meet-join**, the first 0.11 brick (the isolated Google Meet joining layer). Everything you need to run, observe, and verify is here. No infra, no full stack — one container + a browser. Develop by editing `src/` and re-running; the harness is hot.

## Objective

Make the brick's behavior **provably correct against ground truth** — never trust the brick's own logs as proof. Concretely:
1. **Admission must be exact** — no false positive (claims admitted while in lobby) and no false negative (sits in call reporting not-admitted). ✅ done & validated; guard against regressions.
2. **Blocks must be named** — when Google's bot-detection blocks the join, emit `JOIN-STATE: blocked` within seconds instead of polling blind. ⬜ open (#444), needs a real block to build against.
3. **Every verdict you fix becomes a fixture** — a DOM snapshot of the state, so CI can replay it (this is the MVP2 capture-format work; until then, live cross-check is the oracle).

## The loop (hot — edit src/, re-run, no rebuild)

```bash
# build the env image ONCE (cached after first build; ~2 min)
make image

# run the brick against a meeting, source mounted live, lens at noVNC:
docker rm -f mj-debug 2>/dev/null
docker run --name mj-debug -p 6080:6080 \
  -v "$PWD/src:/pkg/src" -v "$PWD/scripts:/pkg/scripts" \
  -e MEETING_URL="https://meet.google.com/xxx-xxxx-xxx" \
  meet-join-debug > /tmp/mj-run.log 2>&1 &

# watch the bot's own browser:  http://localhost:6080/vnc.html
# read state:  grep -E "JOIN-STATE|ADMIT-DUMP|RESULT" /tmp/mj-run.log
```

- `make debug URL=…` wraps the run. `make debug-cloud URL=… CLOUD_HOST=<host>` runs the **same image from another egress IP** (rsync'd live): `bbb` = 2nd household; a throwaway Linode VM = the datacenter/production position.
- `DEBUG_ADMISSION=1` (set in the container entrypoint) emits `🔎 [ADMIT-DUMP …]` lines: `waitingRoom`, `realTiles`, raw `participantTiles` with ids/labels, `recaptchaFrames`. This is your diagnostic window.
- Container-only by design (`debug-join.ts` refuses a bare host): the harness env is reproducible or it is not evidence. On a bare host it silently falls back to synthetic input and lies.

## The oracle: YOU are both host and observer (Claude-in-Chrome MCP)

The live test is fully agent-driven — do not wait for a human:
1. **Create a meeting you control** — `navigate` to `https://meet.google.com/new` in the user's Chrome (tool `mcp__Claude_in_Chrome__navigate`); it lands in a call with admission required. Read the meeting code from the title/URL.
2. **Launch the bot** at that URL (loop above).
3. **Read ground truth from the host tab** — open the People panel; "WAITING TO JOIN" vs "IN THE MEETING" + the participant count is **authoritative**. Screenshot it.
4. **Admit the bot** — click Admit in the host People panel.
5. **Cross-check**: the brick's `ADMIT-DUMP` / `JOIN-STATE` must match what the host tab shows at each moment. A mismatch is the bug.

> **The brick's `admitted=true` on a DOM selector is a CLAIM, not proof. The host's participant list is the oracle.** This is non-negotiable — it's how the admission false-positive (below) was caught.

## What is PROVEN (don't redo; protect from regression)

- **Humanized X11 join + admission works** from residential IP — bot reaches lobby, host admits, bot enters call. Verified vs host list, multiple fresh meetings.
- **Admission oracle is exact.** `countRealParticipantTiles()` excludes the self "Backgrounds and effects" phantom (Google tags it `[data-participant-id]` too, present in BOTH lobby and call). Validated: lobby `realTiles=0`, admitted `realTiles=1` (the host tile).
- **Docker image builds** from repo-root context with the brick resolving inside it (`vexa-bot:mvp1-local`).
- **Synthetic input (bare host) → hard black-page block**; humanized → clean join. The block correlates with **input stack**.

## What is OPEN (the work)

1. **#444 — the real block detector.** The `blocked` JoinState + `callBlockedCallback` exist but are NOT emitted (an earlier reCAPTCHA emit was a false positive — Google loads invisible reCAPTCHA Enterprise on every join; reverted). A correct detector keys on a **visible** challenge / blank block page, and needs a run that actually **reproduces a block** to build without false positives → run `make debug-cloud CLOUD_HOST=<linode>` (datacenter egress) to get one.
2. **Solo-admitted edge** — bot admitted with zero humans present yields `realTiles=0`, so that case still leans on `waitingRoom=false`. Document/handle.
3. **Fixtures** — capture DOM snapshots of lobby / admitted / blocked states into `fixtures/`; they are the `gate:oracle` replay inputs (MVP2 `capture.v1`). Live cross-check is the oracle only until these exist.

## Gates (laptop, no infra)

```bash
node scripts/check-isolation.js   # gate:isolation — no import escapes the package
npm run build                     # gate:standalone — own deps, tsc clean
npx tsc --noEmit                  # typecheck
```

## Context

Brick lives at `packages/meet-join/`; consumed by `services/vexa-bot` (the bot imports it — never the reverse). Pack: #443. Spec: repo-root `MANIFEST.md`. When you fix a verdict, log it on the relevant issue with the host-vs-brick evidence, commit on `pack/0.11-mvp1-meet-join`, push to `stitchfork`.
