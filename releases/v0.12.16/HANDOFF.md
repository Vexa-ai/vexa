# v0.12.16 — release handoff

**Status: staged on real staging, awaiting owner sign-off. NOT merged, NOT tagged, NOT promoted.**

Baseline `v0.12.15` (last *shipped*, signed receipt), so this batch is exactly the 7 PRs below.

---

## TL;DR — what's left is yours

1. **`state: value-signed`** on the 7 PRs (D9). Branch protection (`merge-card`, enforce_admins) holds the merge until then — even you can't bypass.
2. **Fix the `sbom` job** (fails on rc.3) — it must pass before the real v0.12.16 tag, or the promote can't attest.
3. Open the release PR → merge → tag `v0.12.16` → promote (owner env approval).

Everything up to the sign line is done. Live staging is running the exact release content.

---

## Branches

| ref | commit | contents |
|---|---|---|
| `rc/0.12.16` | `f0c6bac7` | the release: 7 PRs + 5 integration fixes + version bump + changelog |
| `release/0.12.17` | `8f600aba` | the in-flight STT carrier (deferred), as 0.12.17's first item |
| `origin/main` | `bf13dc54` | unchanged (v0.12.15 receipt) — nothing merged yet |

Published images: `vexaai/v012-*:v0.12.16-rc.3` (from `f0c6bac7`).
Version stamps at `0.12.16`: `package.json`, `Chart.yaml` appVersion, `docs-reflects`. 5 changelog fragments folded.

---

## The batch — 7 PRs, 5 external contributors

| PR | issue | contributor | value | witness |
|---|---|---|---|---|
| #485 | #545 | **Mohammad Tauqueer** | bot alone → `left_alone`, stops burning capacity | **LIVE ✅** (full seq: silence verdict → browser leave → completed) |
| #810 | #537 | **LauraGPT** (Zhifu Gao, FunASR lead) | bring-your-own OpenAI-compatible STT, documented | desk (contract verified line-by-line) |
| #821 | #819 | **Ahmed Tokyo** | lite bot no longer echoes in Meet | **LIVE ✅** (mute both sinks + in-call, no echo) |
| #822 | #820 | **Ahmed Tokyo** | redis first-use connect race fixed | desk (offline red→green, 3-arm) |
| #836 | #552 | Dmitriy Grankin | degraded meeting reports WHY (terminal event) | desk (offline red→green) |
| #855 | #843 | Dmitriy Grankin | malformed `native_meeting_id` → typed 422, not 500 | **LIVE ✅** (7-case boundary on staging-lite) |
| #860 | — | **Jacob Schooley** | meeting timestamps serialize as UTC (Z) | desk (unit-pinned) |

**Live witness (2026-07-21):** the owner joined a real Google Meet, transcription flowed (37 rows via local whisper), no echo, then left — the bot fired `left_alone` within the window (#545 witnessed end-to-end). Plus a second `left_alone` in the record from the prior day.

**Sign-off constraints (D9):** #855 and #860 are maintainer-authored → a **non-author** signs. #821/#822 are a-tokyo's (reporter *and* author) → **not** a-tokyo. #810 (LauraGPT) and #485 (m-tauqueer) are external.

---

## Integration fixes (maintainer, each found *after* the first "33/33 green" — each a red→green)

Every one of these came from adversarial review or the owner's questions, **not from CI** — the standing lesson of this release.

| commit | fix | found by |
|---|---|---|
| `e989301c` | #485 made `aloneness` a required orchestrator dep; #836's test omitted it → batch RED | clean gate run |
| `60555fc0` | #822's memo made `quit()` hang forever on an unreachable redis | 13-agent adversarial review, reproduced independently |
| `f0c6bac7` | aloneness tap re-judged delivered frames by RMS vs the capture gate's peak → quiet speech counted as silence | owner's question |
| `07083e80` | docker-backend test used a fixed container name; 30+ worktrees on one daemon collided → false reds | diagnosing a false red |
| `34df8b55` | custom-STT FunASR/SenseVoice example marked community-contributed-and-unwitnessed | review |

---

## Staging — LIVE

**`vexa-v012-staging`** (LKE), helm release `vexa` **rev10** @ `v0.12.16-rc.3`, front door `oss.staging.vexa.ai`.

- All 6 services on rc.3; 13/13 pods ready, **0 restarts**; `helm --wait` green.
- Gateway `/health` 200; typed `401` auth; DB converged v0.12.9→rc.3 with no crashloop.
- Transcription (`whisper-cpu`) + state (postgres/minio/redis) **preserved** — `--reuse-values`, only the image tag bumped.
- **Rollback:** `helm rollback vexa 9` (→ v0.12.9), one command.
- Access: `sops -d ~/dev/vexa-secrets/no-prod/kubeconfig-stage.enc.yaml`; namespace `vexa-v012-staging`.

**Open on staging:** `oss.staging.vexa.ai` returned `000` externally — the app is healthy (in-cluster 200), so it's the **external Caddy** front door, deliberately not touched. Repoint/cert-check it if you want the public URL serving rc.3.

---

## Blockers before promote (in order)

1. **7 value-signs** — the merge bar. Nothing merges without them.
2. **`sbom` job fails on rc.3** — supply-chain attestation, orthogonal to image correctness (all 11 images built + published fine), but the promote pipeline attests it. **Must pass before the real v0.12.16 tag.** Re-run or diagnose the syft/attestation step.
3. **`witness.json`** — generates only after the `v0.12.16` tag exists (`RELEASE_VERSION=v0.12.16 … node scripts/release-witness-script.mjs`). Classify each PR; the LIVE ones above are `witnessed:true`, the desk ones `by-proxy` with named evidence.

## The promote sequence (once 1–2 clear)

```
# owner value-signs the 7 PRs → merge-card clears
# open + merge the release PR (rc/0.12.16 → main); observation bundle drafted at
#   scratchpad/release-pr-body.md
git tag v0.12.16 <merge-commit> && git push origin v0.12.16   # fires release-images + release-validate (promote:false)
# generate + commit releases/v0.12.16/witness.json, fill witnessed_by/at, sign
# dispatch release-validate promote:true → approve the release-promote environment (owner act)
gh release create v0.12.16 --verify-tag --latest --notes-file <notes>
```

**Merge mechanics — one open decision:** the batch is assembled on `rc/0.12.16` as `--no-ff` merge commits (authorship preserved in git). Options: (a) **integration PR** `rc/0.12.16 → main` — fast, git authorship intact, credit the 5 contributors in release notes + on their PRs, but the 7 won't show GitHub's "merged" badge; (b) **per-PR merge** — each gets the badge, but the 5 integration fixes must be distributed back onto the fork branches (more work + re-runs). Recommendation: (a).

---

## Follow-ups filed this session (not blocking v0.12.16)

- **#863** `good-first` — validate FunASR/SenseVoice independently (the #810 example is unwitnessed)
- **#864** — docker-backend fixed container name (fixed in-batch; the wider `COMPOSE_PROJECT` default collision noted on it, deferred)
- **#865** — no shared orchestrator test double; every new construction site rediscovers `aloneness` by crashing
- **#866** — `left_alone` never fires on Zoom/Teams/Jitsi when the bot is alone from the start (readiness latches on first audio, not on capture attached). **Blocks #545's platform-agnostic claim**; the shipped docs overclaim.

## Deferred to v0.12.17

- The **in-flight STT-fault carrier** (`release/0.12.17` @ `8f600aba`) — #836 ships here reporting on the terminal event; the mid-meeting banner (bot→collector→SSE→terminal's existing `model-error` renderer) is built, unit-tested (collector 4/4 red→green, bot 423 checks), **unwitnessed live**. First item for 0.12.17.
- The **`platform_settings.transcription` override** that silently outranks `TRANSCRIPTION_SERVICE_URL` — the actual point of introduction behind the 402 incident. Highest-value follow-up; not yet filed.
