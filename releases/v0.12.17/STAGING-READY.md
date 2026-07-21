# v0.12.17 — STAGING-READY

**Status: rc/0.12.17 is staged and validated for a staging deploy. NOT deployed** (staging is
busy running 0.12.16-rc.3). When staging frees, the deploy is the single `helm upgrade` below.

RC tip: **`ca675774`** (rc/0.12.17). Images published to Docker Hub at tag **`v0.12.17-rc.1`**
(built from `ca675774`, linux/amd64 — LKE node arch). No git tag was cut (see "Image strategy").

---

## What this RC contains beyond 0.12.16

Baseline is the 0.12.16 line (rc.3 on staging). On top of that, rc/0.12.17 =
the 0.12.17 quality line + #868 + gmeet cadence fixes, **plus this session's fold** (`9fbe8a1e → ca675774`):

| commits | issue | value |
|---|---|---|
| `8cf41c7b` | #870/#797 | msteams: a speaker signal the resolver cannot name is REPORTED, not silently dropped |
| `9f7a29a9` `7dd46cf7` `5f75d75f` | #896/#853 | msteams: structural leaf-scan name fallback when hashed name-classes drift + offline red→green regression + Mute/Unmute-leaf guard |
| `d22e5476` `ca675774` | #885/#865 | bot: shared orchestrator test-doubles + fail-loud on a missing required port (reconciled with rc's existing aloneness wiring) |

(`eabb92c1`, the zoom windowless-DOM blind-report, was **skipped — empty**: rc already carries the
equivalent. Expected per the fold plan.)

Content verified in-tree: `extractTeamsSpeakerName` + `"SPEAKING SIGNAL with NO NAME"` +
`'mute'` in the forbidden list in `msteams-speakers.ts`; `blindness.test.ts` + `name-drift.test.ts`
present; `REQUIRED_PORTS` in `orchestrator.ts` names `'aloneness'` (both intents kept) and
`test-doubles.ts` present.

---

## THE deploy command (one command, run when staging frees)

```bash
# decrypt the staging kubeconfig in-memory (age key at ~/.config/sops/age/keys.txt)
KC=$(mktemp) && sops -d ~/dev/vexa-secrets/no-prod/kubeconfig-stage.enc.yaml > "$KC"

KUBECONFIG="$KC" helm upgrade vexa deploy/helm/charts/vexa \
  -n vexa-v012-staging --reuse-values \
  --set global.imageTag=v0.12.17-rc.1 --wait

rm -f "$KC"
```

`--reuse-values` preserves all live state config (postgres/redis/minio PVCs, the pre-created
`vexa-v012-secrets`, the cross-namespace transcription-gateway URL); **only `global.imageTag`
moves** `v0.12.16-rc.3 → v0.12.17-rc.1`. This is the exact rev10 pattern from the 0.12.16 handoff.
Run it from the repo root of an `rc/0.12.17` checkout (chart path is repo-relative).

- Namespace: `vexa-v012-staging` · release: `vexa` · current rev **10** (`0.12.16`, deployed) →
  deploy creates rev **11**.
- Front door `oss.staging.vexa.ai` is the external Caddy (returned `000` externally at rev10 while
  the app was healthy in-cluster) — a separate concern, not touched by this deploy.

---

## Published images — `v0.12.17-rc.1` (linux/amd64, from `ca675774`)

All 8 refs the staging chart renders. Digests are the current registry manifests at the tag
(what the deploy pulls):

| image | manifest digest |
|---|---|
| `vexaai/v012-gateway:v0.12.17-rc.1`      | `sha256:2fcb516f955b2b142ccb2ce7f613be36498f1f6561b54bb573a66c5bb0f619e2` |
| `vexaai/v012-admin-api:v0.12.17-rc.1`    | `sha256:beda4b8e88b8b794acb12c05b4d1e90be9321d343276bd35cf7344e279557128` |
| `vexaai/v012-runtime:v0.12.17-rc.1`      | `sha256:7d313189100879139172f4e80f425d0d429a0cfb51f1a56b7eaacb3817344535` |
| `vexaai/v012-agent-worker:v0.12.17-rc.1` | `sha256:5a9fd2ace5f200017f6c800dd7d2e64f46fa4eed628e6ac9c32c12772296f901` |
| `vexaai/v012-agent-api:v0.12.17-rc.1`    | `sha256:152e7a8de73fd303c47994f65f11ebe9b75c8efb32b02420f36097012be93448` |
| `vexaai/v012-meeting-api:v0.12.17-rc.1`  | `sha256:4d584fcb1474b7f3d7c9dac9728525d9a60a1da8d8c247f8c9fffaaae982dac7` |
| `vexaai/v012-terminal:v0.12.17-rc.1`     | `sha256:bde8e3996a653a7d4736f811a38eb7df2f043fe7484e50e751e32ca527bd6931` |
| `vexaai/vexa-bot:v0.12.17-rc.1`          | `sha256:80f45f972e421c45059c88cea3f731be685592a6f61df04fd4af12bf92b52dae` |

The bot carries the Teams/gmeet fixes (browser-utils bundle). The 7 control-plane services have
build contexts that are **git-proven byte-identical** `9fbe8a1e → ca675774` (the fold changed only
`teams-capture/*` and `bot/src/*`, which none of those Dockerfiles COPY), so they are content-equal
to the pre-fold build; only the bot's content actually changed.

### Image strategy: Option B (local build, no git tag) — chosen

- **Why not Option A (cut `v0.12.17-rc.1` git tag → `release-images.yml`):** `release-images`
  preflight cross-checks the tag base against `package.json` version **and** `Chart.yaml`
  appVersion — both are still `0.12.16` on rc/0.12.17, so a `v0.12.17-*` tag would **fail preflight**
  without a stamp bump. A prerelease tag would NOT promote (the workflow's validate leg runs
  `promote:false`) and `release-published-guard` exempts prereleases, so Option A is *safe* from
  promote/ordering — but it needs a stamp-bump commit + an origin tag firing full CI. Not needed for
  a staging RC.
- **Option B** builds the same Dockerfiles/contexts the release matrix uses, pushed to
  `vexaai/v012-*:v0.12.17-rc.1` with **no git release tag** — exactly what a staging RC needs.
  Push creds: `vexaai` account from `~/dev/vexa-secrets/no-prod/stage-dockerhub-secret.enc.yaml`
  (sops, in-memory). Built linux/amd64 only (LKE = amd64); the release CI path remains available if
  multi-arch + SBOM are ever wanted for a real 0.12.17.

---

## Helm validation (dry-run only — NO live apply)

- `helm lint deploy/helm/charts/vexa -f …/values-staging.yaml` → **0 failed** (only the cosmetic
  "icon is recommended" info).
- `helm template … -f values-staging.yaml --set global.imageTag=v0.12.17-rc.1` → renders **26
  objects**, all **8** image refs resolve to `…:v0.12.17-rc.1`, **zero** `:dev` / `:v012` /
  unpinned leaks.
- Chart is **unchanged** by this session's fold (`git diff 9fbe8a1e..ca675774 -- deploy/helm` is
  empty).
- Diff vs the **0.12.16 chart currently on staging** (rev10): rc/0.12.17 adds exactly **one** new
  meeting-api env var, `CAPTURE_SIGNAL_ENABLED` (values.yaml `meetingApi.captureSignalEnabled`).
  It is **inert under `--reuse-values`**: the template renders it `| default "false"` and the
  values default is `"false"`, so with `--reuse-values` not supplying the key it still renders
  `CAPTURE_SIGNAL_ENABLED="false"` (verified) — capture-recording stays OFF, no missing-value
  failure. Net effect at deploy: the 8 image tags move `-rc.3 → -rc.1` and meeting-api gains that
  one env var at its safe default; `--reuse-values` holds all live state.
  `helm diff` plugin not installed locally; the template-vs-template comparison stands in for it.

## Rollback (one command)

```bash
KUBECONFIG="$KC" helm rollback vexa 10 -n vexa-v012-staging --wait
```

Rev **10** is the current-good `v0.12.16-rc.3`. (History also holds rev 9 = `0.12.9`, the
0.12.16-handoff rollback target, if a deeper rollback is ever needed.)

---

## Green vs pending

**Green (done this session):**
- [x] Teams fix chain (#870/#896) + community #885 folded into rc/0.12.17 (`ca675774`).
- [x] `@vexa/teams-capture` build + tests green (blindness 5/5, name-drift 6/6).
- [x] `@vexa/bot` build + full suite green (orchestrator, stt-faults, stress, aloneness — 0 failures).
- [x] `node scripts/gates.mjs all` **BLOCKING green** (all 21 gates ✓; the one docker-backend red
      was a pristine-machine artifact — `DockerBackend` doesn't pre-pull stock `alpine`; test file
      is byte-identical to base and the fold touches no `core/runtime/` file; green once `alpine`
      cached).
- [x] All 8 staging images published at `v0.12.17-rc.1` from `ca675774`.
- [x] Helm lint + template validation green; rollback command confirmed.

**Pending — upstream gates before an actual staging deploy runs (NOT this task's to clear):**
- [ ] **Human TAKE review** of the RC (maintainer triage per the D-book).
- [ ] **Ship order: 0.12.16 must ship first.** Staging currently runs 0.12.16-rc.3; 0.12.16 is
      itself un-merged/un-tagged/un-promoted (its own value-signs + `sbom` fix outstanding).
      0.12.17 sits on top of that line.
- [ ] **Live witnesses in flight** (do not gate the *staging* deploy, but gate the value claim):
      **#887 Jitsi** and the **muted-join / no-echo** leg.
- [ ] Staging free (it is busy now) → run the one deploy command above.

**Not a staging blocker, noted for a real 0.12.17 release later:** version stamps
(`package.json`, `Chart.yaml` appVersion) are still `0.12.16`. A real `v0.12.17` git tag / GitHub
release would need them bumped (release-images preflight) + the witness/value gates; none of that is
required for the RC-on-staging path.
