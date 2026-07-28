# Alloy Meet Audio-Boundary Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Localize the blocked Google Meet witness to the disposable speaker or the first damaged
Vexa producer boundary, then close two exact-candidate live runs before fast-forward integration.

**Architecture:** Keep one Meet audio transport and tee its existing boundaries. The disposable
speaker gets an isolated container/profile/PulseAudio graph; Vexa keeps its product recording,
`VEXA_CAPTURE_SIGNAL`, and transcribe taps. Run H1, H2, and H3 sequentially and stop as soon as one
is confirmed.

**Tech Stack:** Docker Engine through Ubuntu WSL2, PulseAudio, Chromium/Playwright, Node.js,
FFmpeg/FFprobe, Vexa Lite, faster-whisper, Google Meet, Git.

## Global Constraints

- Execute inline in the session-owned branch/worktree; do not use parallel agents on this path.
- Preserve all pre-existing containers, volumes, networks, branches, worktrees, and dirty files.
- Use only names prefixed `alloy-meet-audio-20260728-` for runtime resources.
- Store runtime artifacts under ignored
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/`.
- Keep the source WAV byte-identical for H1 and H2.
- Change one hypothesis variable at a time; after three disproved hypotheses, stop.
- Do not modify production source unless a product producer defect is localized and has a focused
  RED.
- Apply DRY and SOLID proportionately: one diagnostic tap per existing boundary, one responsibility
  per helper, configuration instead of copied paths, and no unrelated refactor.
- Run exact named tests with time bounds before broad gates.
- Do not push.

---

### Task 1: Build the ignored boundary-analysis harness

**Files:**

- Create ignored:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/analyze-audio.mjs`
- Create ignored:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/run-isolated-speaker.mjs`
- Create ignored:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/materialize-captured-signal.mjs`
- Read: `core/meetings/services/bot/src/telemetry.ts`
- Read: `core/meetings/services/bot/src/capture-bridge.ts`

**Interfaces:**

- Consumes: an audio file or captured-signal JSONL and an explicit output directory.
- Produces: boundary `.f32le` files plus one JSON stats record per file:
  `{sample_rate, channels, samples, duration_sec, peak, peak_dbfs, rms, rms_dbfs,
  clipping_samples, pcm_sha256}`.

- [ ] **Step 1: Create `analyze-audio.mjs`**

Use FFmpeg to decode the input to mono Float32 LE at 16 kHz, persist the decoded PCM, then compute
the declared statistics directly over the Float32 samples. Count clipping only when
`abs(sample) >= 0.999`.

- [ ] **Step 2: Prove the analyzer on the retained source WAV**

Run:

```powershell
node .superpowers/sdd/tmp/alloy-meet-audio-20260728/analyze-audio.mjs `
  F:\vexa\.superpowers\sdd\worktrees\alloy-rnd-code-switch-20260728\.superpowers\sdd\tmp\alloy-code-switch-20260728\mixed-en-ru-en.wav `
  .superpowers/sdd/tmp/alloy-meet-audio-20260728/source
```

Expected: `sample_rate=16000`, `channels=1`, duration `15.3709375`, peak below `1`, zero clipping,
and source SHA-256
`fa33da1e2ef2b11b88dd52b53698ba379243e007914684b3b255cb25f103c7c2`.

- [ ] **Step 3: Create `materialize-captured-signal.mjs`**

Decode each `CapturedFrame.pcm` base64 payload as Float32 LE, preserve frame order, group by
`speakerIndex`, and materialize one PCM file per channel plus a frame manifest. Reject a
non-monotone `seq`, invalid `pcm_len`, or non-finite sample.

- [ ] **Step 4: Create `run-isolated-speaker.mjs`**

Reuse `getJoinBrowserArgs()` and `joinMeeting()`. Launch one ephemeral Chromium context inside the
speaker container, log the actual `getUserMedia` constraints, use the container's own
`virtual_mic`, and play the retained WAV through its own `tts_sink`. Concurrently:

- record `virtual_mic` with `parec`;
- tee the actual audio `RTCRtpSender.track` with `MediaRecorder`;
- record outbound RTP/audio-source statistics;
- log join, unmute, playback, settle, leave, and cleanup events.

The script accepts `--processing=default|disabled`. `disabled` changes only
`autoGainControl`, `noiseSuppression`, and `echoCancellation`.

- [ ] **Step 5: Capability-probe the candidate image**

Run one bounded `--rm` container from
`vexa-lite:alloy-code-switch-20260728-59bc7e9` and prove `node`, Chromium, Xvfb, `pulseaudio`,
`pactl`, `parec`, `paplay`, `ffmpeg`, the join dist, and Playwright are present.

Expected: exit 0; no persistent container remains.

---

### Task 2: Start an exact isolated candidate stack

**Files:**

- Runtime artifacts only:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/runtime/`
- Read: `deploy/lite/Makefile`
- Read: `deploy/lite/entrypoint.sh`

**Interfaces:**

- Consumes: candidate image ID
  `sha256:96caccf76e9c861fcf48e90ac0f3476b111646ba9497c9368e76a97b6a97fc8a`.
- Produces: a healthy isolated Lite gateway, Terminal, Whisper, Postgres, and MinIO.

- [ ] **Step 1: Inventory and freeze pre-existing runtime identities**

Record IDs, names, images, state, ports, networks, and volumes without printing environment values.

Expected: no session-prefixed resource exists.

- [ ] **Step 2: Select unused loopback ports**

Use `28056` for gateway, `23001` for Terminal, `28100` for agent API, and `28000` for Whisper only
if each is absent from the listening socket inventory. Stop on a collision.

- [ ] **Step 3: Start session resources**

Create network `alloy-meet-audio-20260728-net`, fresh Postgres and MinIO volumes, and containers:

```text
alloy-meet-audio-20260728-postgres
alloy-meet-audio-20260728-minio
alloy-meet-audio-20260728-whisper
alloy-meet-audio-20260728-app
```

Use `--restart=no`, `Systran/faster-whisper-small`, and exact Alloy flags:

```text
ALLOY_STT_MAX_CONCURRENCY=1
ALLOY_STT_CHANNEL_BACKPRESSURE=1
ALLOY_STT_LANGUAGE_MODE=auto
ALLOY_STT_TELEMETRY=1
VEXA_CAPTURE_SIGNAL=1
VEXA_CAPTURE_SIGNAL_DIR=/evidence/captured-signal
VEXA_RECORDING_TIMESLICE_MS=5000
```

Mount only the session evidence directory at `/evidence`.

- [ ] **Step 4: Poll bounded readiness**

Poll Postgres, MinIO, Whisper direct `/health`, gateway `/health`, and Terminal with fixed
deadlines. Do not trust a container health label as the only HTTP proof.

Expected: all direct probes green; candidate image revision label is
`59bc7e925cf9ee173fd3262963b7b47c6d3c1bcd`.

- [ ] **Step 5: Run the existing Lite probe**

Run `deploy/lite/probe.sh` with the session container/ports and a process-only local admin token.
Keep the token out of artifacts and command output.

Expected: token mint and authenticated endpoints succeed.

---

### Task 3: Run H1 with a separate guest container

**Files:**

- Runtime artifacts:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/h1/`
- Modify after verdict:
  `docs/superpowers/evidence/2026-07-28-alloy-stt-code-switch.md`

**Interfaces:**

- Consumes: healthy Task 2 stack, signed-in host Meet session, retained source WAV.
- Produces: source, virtual-mic, sender, product-recording, Vexa-capture, Whisper, and API evidence.

- [ ] **Step 1: Create a fresh Meet from the signed-in host**

Keep the host microphone muted. Retain the native meeting ID only in process state and redact the
invite URL/code from durable logs.

- [ ] **Step 2: Start the Vexa listener through the product API**

POST one `google_meet` bot with a fresh name, transcription enabled, recording enabled, and no
language pin. Admit it from the host UI and wait for `active`.

- [ ] **Step 3: Start the isolated default-processing guest**

Run a second container named `alloy-meet-audio-20260728-speaker-h1` from the candidate image with
its own Xvfb, PulseAudio graph, Chromium context/profile, and the runtime harness. Use
`--processing=default` and a never-before-used guest name.

Expected: only the guest container changes the guest `virtual_mic`; the app container's sink/source
state and volume remain unchanged.

- [ ] **Step 4: Collect and materialize all boundaries**

Collect:

- source WAV and decoded PCM;
- guest `parec` virtual-mic PCM;
- guest sender-track recording and RTP/audio-source stats;
- Vexa product recording;
- Vexa captured-signal JSONL and materialized channel PCM;
- existing `.stt.jsonl`;
- Whisper logs and Meeting API transcript.

Stop the session bot and guest cleanly before analysis.

- [ ] **Step 5: Analyze H1**

Expected confirmation:

- source, virtual-mic, and sender have bounded duration, zero clipping, and no repeated waveform;
- Vexa capture is no longer near-full-scale/clipped;
- API contains recognizable EN → RU → EN in order.

If confirmed, keep the isolated topology and skip Tasks 4 and 5. If disproved, record exactly where
the first material degradation appears and continue to H2.

---

### Task 4: Run H2 and H3 only when required

**Files:**

- Runtime artifacts:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/h2/`
- Runtime artifacts:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/h3/`

**Interfaces:**

- Consumes: the H1 stack and source WAV.
- Produces: one-variable verdicts for Chromium processing and source/pre-WebRTC input.

- [ ] **Step 1: H2 Expected**

Disabling guest `autoGainControl`, `noiseSuppression`, and `echoCancellation` while leaving every
other input unchanged repairs the first sender/remote degradation.

- [ ] **Step 2: Run H2 once**

Use a fresh guest name and `--processing=disabled`. Do not change Pulse volume, WAV bytes, Meet,
candidate, or listener configuration.

- [ ] **Step 3: Record H2 Actual and Verdict**

If confirmed, keep disabled processing in the stand and proceed to Task 6. If disproved, proceed to
H3 without an unchanged retry.

- [ ] **Step 4: H3 Expected**

The source or virtual-mic measurement violates mono 16 kHz, bounded peak/RMS, or clipping
requirements before WebRTC.

- [ ] **Step 5: Run H3 once**

Inspect the byte-identical source and virtual-mic capture. Change format/level only if the measured
contract is invalid; derive the correction from the measurement rather than trying percentages.

- [ ] **Step 6: Record H3 Actual and Verdict**

If H3 is disproved, stop. Do not attempt a fourth hypothesis or change production code.

---

### Task 5: Fix a localized Vexa producer defect with TDD only when required

**Files:**

- Test and modify only the producer module where clean remote PCM first becomes damaged.
- Likely test seam:
  `core/meetings/modules/gmeet-capture/src/gmeet-capture.test.ts`
- Likely producer:
  `core/meetings/modules/gmeet-capture/src/pcm-capture.ts`

**Interfaces:**

- Consumes: clean upstream PCM and a repeatable damaged Vexa capture.
- Produces: one focused RED/GREEN regression and the minimal producer-boundary fix.

- [ ] **Step 1: Invoke `superpowers:test-driven-development`**

Do this before changing production source.

- [ ] **Step 2: Add the smallest failing reproduction**

The test must reproduce the measured corruption without a live meeting and must fail for the exact
bad duration/peak/repetition property.

- [ ] **Step 3: Run the exact test and retain RED**

Use the owning package's single named test with a bounded timeout. Stop if it does not fail for the
expected reason.

- [ ] **Step 4: Implement one producer fix**

Do not alter Whisper, transcript assembly, or API rendering.

- [ ] **Step 5: Run RED → GREEN and owning narrow checks**

Run the named test, owning package build, and isolation check. Commit only the proven files.

---

### Task 6: Close two fresh exact-candidate Google Meet witnesses

**Files:**

- Runtime artifacts:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/witness-1/`
- Runtime artifacts:
  `.superpowers/sdd/tmp/alloy-meet-audio-20260728/witness-2/`

**Interfaces:**

- Consumes: the calibrated stand and unmodified exact candidate, or the committed producer fix.
- Produces: two sequential live acceptance rows.

- [ ] **Step 1: Remove diagnostic code overlays**

Prove the app runs the exact tracked candidate bytes. Existing opt-in capture/telemetry recording may
remain enabled; no instrumented built-file mount may remain.

- [ ] **Step 2: Run witness 1**

Use a fresh Meet and guest identity. Require recognizable EN → RU → EN, `mul` or honest segment
languages, monotone bounded timestamps, and no clipping, repeats, or hallucinations.

- [ ] **Step 3: Run witness 2**

Run sequentially after witness 1 cleanup with another fresh guest identity. Apply the same
acceptance; do not reuse the first meeting as a retry.

- [ ] **Step 4: Record the two verdicts**

Any red keeps `main` unchanged and stops before broad gates.

---

### Task 7: Verify and commit the exact candidate

**Files:**

- Modify:
  `docs/superpowers/evidence/2026-07-28-alloy-stt-code-switch.md`
- Already added:
  `docs/superpowers/specs/2026-07-28-alloy-meet-audio-boundary-diagnostic-design.md`
- Already added:
  `docs/superpowers/plans/2026-07-28-alloy-meet-audio-boundary-diagnostic.md`

**Interfaces:**

- Consumes: green Task 6 evidence.
- Produces: one clean, fully verified R&D tip.

- [ ] **Step 1: Run focused checks**

Run only tests/build/isolation for modules changed since `458e025`.

- [ ] **Step 2: Build clean Lite from the exact commit**

Use a tracked-only archive, no `.git`, `.env`, package store, or runtime artifacts. Record commit,
tree, archive SHA-256, image ID, labels, and direct runtime health.

- [ ] **Step 3: Run the full Linux gate once**

Run `node scripts/gates.mjs all` only after focused checks and both witnesses are green. Record exit
status and pass/skip counts.

- [ ] **Step 4: Update evidence**

Append Expected → Actual → Verdict for branch cleanup, H1/H2/H3 actually run, boundary metrics, two
witnesses, exact-candidate build, gates, omissions, and cleanup. Do not record credentials or Meet
invite codes.

- [ ] **Step 5: Verify and commit**

Run `git diff --check`, inspect the complete scoped diff, stage exact files, commit, and require a
clean worktree.

---

### Task 8: Fast-forward main and remove R&D state

**Files:**

- Git refs/worktrees only.

**Interfaces:**

- Consumes: clean verified R&D tip and unchanged clean `main`.
- Produces: one clean `main` worktree/branch with no R&D branches and no push.

- [ ] **Step 1: Prove integration preconditions**

Require clean `main`, clean R&D worktrees, `git merge-base --is-ancestor main <R&D-tip>`, and no
R&D-only commit absent from the retained tip.

- [ ] **Step 2: Fast-forward**

Run `git merge --ff-only` in `F:\vexa`. Require `main` and the verified R&D tip to resolve to the
same SHA.

- [ ] **Step 3: Remove session resources**

Archive the boundary PCM, manifests, and logs under
`F:\vexa\.superpowers\sdd\runtime-evidence\alloy-meet-audio-20260728\`, then remove only
`alloy-meet-audio-20260728-*` containers, network, volumes, and the redundant ignored copies in the
worktree. Prove pre-existing runtime IDs/states are preserved.

- [ ] **Step 4: Remove R&D worktrees and branches**

Remove both R&D worktrees only after their tips are ancestors of `main`, then use `git branch -d`
for:

```text
alloy/vexa-rnd-code-switch-20260728
alloy/vexa-rnd-meet-witness-20260728
```

- [ ] **Step 5: Final verification**

Require one worktree at `F:\vexa`, one local branch `main`, clean status, and no push.
