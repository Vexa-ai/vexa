# v0.12.18 — dev hot-loop plan (restarted from state)

Date: 2026-07-23 · Branch: `rc/0.12.18` @ `b06a5e49` · Gates: green (33, real compose stack)

Produced by `dev:plan`. Maps every open scope item to its module (MANIFEST §1), its harness,
and whether the infra to witness it exists **today**.

---

## 0. State this restarts from

15 PRs merged into `rc/0.12.18`; every one carries an offline red→green bundle and gates green.
**The remaining work is overwhelmingly witnessing, not coding.** That reframes the plan: the
question is no longer "what do we build" but "what rig proves it, and does that rig exist".

Merged: #894·532 · #885·865 · #903·803 · #904·841 · #906·809 · #907·600 · #910·892 · #911·893 ·
#912·718 · #913·889 · #916·915 · #917·846 · #918·840 · #919·862 · #920·795.

---

## 1. Infra inventory — verified, not assumed

Checked by building and booting the rig on 2026-07-23:

| asset | state | note |
|---|---|---|
| `vexa/meet-join-env:dev` | ✅ present + tagged | the ~3.6 GB env; **never build it** — pull `vexaai/meet-join-env:dev`, retag |
| `meet-join-debug:latest` | ✅ **built this session** | `make image` took seconds off the warm env |
| noVNC human view | ✅ **HTTP 200 verified** | `http://localhost:6080/vnc.html` |
| CDP agent control | ✅ advertised by the rig | `playwright connectOverCDP("http://localhost:9222")` |
| live source mount | ✅ | host edit + `make debug` again = instant (tsx, no rebuild) |
| brick logs | ✅ | `>>> [JOIN-STATE] …`; `[ADMIT-DUMP …]` under `DEBUG_ADMISSION=1` |
| `mock-bot:dev` | ✅ built | **required** for `gate:compose` since #718 (see §5) |
| throwaway VM | ❌ none | needed for egress-sensitive gmeet legs (§3) |
| **Jitsi support in the rig** | ❌ **absent** | `debug-join.ts` accepts google-meet / teams / zoom **only** |

**The CDP channel is the leverage.** Several legs previously assumed to need a human eyeballing
noVNC can instead be asserted programmatically — which selector matched, what the lobby DOM says,
whether a control exists. Human presence is only genuinely required where a **second party must
act** (admit, deny, observe the host-side lobby).

### Finding: the rig cannot drive Jitsi

`scripts/debug-join.ts` rejects a Jitsi URL outright. This matters for #887, whose *remaining*
legs are Zoom + Teams (both supported, so #887 is unblocked) — but any future Jitsi work has no
watch harness. Filed as a gap, not worked around.

---

## 2. Per-item plan

Harness modes: **W** = VNC/CDP watch (rig) · **R** = replay/offline · **S** = stage soak ·
**D** = deploy-gated · **H** = needs a human counterparty in the meeting.

### Track 1 — merged, live leg outstanding

| item | module (MANIFEST §1) | mode | infra ready? |
|---|---|---|---|
| #846 gmeet CTA locale | `meet-join`/googlemeet | W+H | rig ✅ · **English-first regression leg is the priority** · forced-locale browser ⚠ · meeting ❌ |
| #840 gmeet denial vs captcha | `meet-join`/googlemeet | W+H | rig ✅ · host must **deny** ❌ |
| #862 pre-active liveness reap | `meeting-lifecycle` + `meet-join` | W+H | rig ✅ · host must admit at **4–5 min** ❌ · needs a control plane (compose) |
| #889 gmeet Stop in lobby | `meet-join` + orchestrator | W+H | rig ✅ · host must leave it knocking ❌ |
| #839 lobby-stop measurement | as #889 | W+H | **piggybacks #889** — same session, add a clock |
| #600 Teams false eviction | `meet-join`/msteams | W+H | rig ✅ · real Teams meeting ❌ |
| #915 Teams login redirect | `meet-join`/msteams | **opportunistic** | rig ✅ · **no deterministic trigger** — see §4 |
| #718 dead-workload → terminal | `bot-orchestration` + client | R+D | compose ✅ — visual row only |
| #841 webhook history | `webhooks` | R ✅ / D | fake-receiver leg **done**; live blocked on `vexa-platform#74` + deploy |
| #803 RSS soak | `meeting-store` | S | ❌ stage/prod — prod held by another session (0.12.16) |
| #893 SCAN saturation | `collector` | S | ❌ stage — oracle is `cmdstat_scan`/s → ~0 |
| #897 Zoom parser | client (dashboard) | D | ❌ hosted dashboard deploy |
| #795 MCP SSE | gateway | R ✅ | **DONE** — live compose witness already recorded |

### Track 2 — blocked on an owner decision (no dev work until answered)

#908/#816 browser_session disposition · #887 Zoom+Teams empty-room witness · #902/#525 version
call · #890 + #861 (coordinate with the v0.12.17 attribution line).

### Track 3 — unstarted, **needs zero new infra** — burn down now

| item | module | mode | infra ready? |
|---|---|---|---|
| #921 mcp sessioned GET never starts | `mcp` service | R | ✅ compose repro documented on the issue |
| #864 test-isolation fixed container name | test lane | R | ✅ |
| #722 compose `BROWSER_IMAGE` forbidden default | deploy/compose | R | ✅ (and see §5 — we hit this live) |
| #717 compose/mac SetupGate credentials | deploy/compose | R | ✅ |
| #653 Redis parity + licensing | deploy/lite | R | ✅ desk |
| #716 hot-reload dev loop undocumented | docs | R | ✅ desk |
| #75 NGINX base URL | docs | R | ✅ desk |

---

## 3. Provisioning decisions

1. **Batch the human legs into two sessions, not seven asks.** #846 · #840 · #862 · #889 · #839
   are all gmeet with a host counterparty — one scheduled Meet session covers all five if the host
   runs a script: leave the bot knocking (→ #889/#839), deny it (→ #840), admit a second bot at
   4–5 min (→ #862), and both joins exercise the CTA path (→ #846). #600 needs one Teams session.
   This is the single highest-leverage move in the plan.
2. **gmeet legs should run `make debug-cloud`, not `make debug`.** The Makefile is explicit:
   *"the egress IP is the only variable (#444: Google's gate keys on IP reputation)"*. A household
   IP does not reproduce the production network position. → provision a throwaway Linode
   (us-sea, ubuntu24.04, tag `throwaway`) and **snapshot/keep it warm** — re-provision + apt Docker
   is the other time sink.
3. **#862 additionally needs a control plane**, since its oracle is the reconcile sweep not
   reaping (`n==0`, row stays `awaiting_admission` past 300s). Run the compose stack alongside the
   rig and watch the meeting row, not only the browser.
4. **Do not build the env image.** Pull + retag. Documented, and re-verified today.

---

## 4. Where this plan deviates from the law

MANIFEST P3: *no harness, no module — an item with no witnessing story goes back to
`scope:prepare-issue`.*

**#915 (Teams sign-in redirect) has no deterministic live trigger.** The redirect is intermittent
and cannot be forced from our side. Strictly, its live leg has no harness.

Ruling: the **fix** does not go back to scope — its offline story is complete and discriminating
(the guard fires on a fabricated sign-in URL; 35/35 green; the 45.7s+30s=75.7s pre-fix burn was
reproduced to the second). What lacks a harness is the *confirmation*, so it is reclassified
**opportunistic-witness**: prod monitoring already caught it three times in one hour, so the next
natural occurrence is the witness, and the observable is now explicit — a `teams_auth_redirect`
reason in `last_error` within ~500 ms instead of a 75 s admission timeout. If it does not recur
within the release's prod tenure, the row is recorded as unwitnessed rather than quietly passed.

---

## 5. Standing hazards for anyone running this loop

- **`gates.mjs all` now needs the mock bot.** Since #718 a dead-at-start workload honestly 502s
  instead of faking a 201, so `gate:compose`'s wizard leg genuinely requires a bot image:
  `docker build -f core/meetings/services/bot/Dockerfile.mock -t mock-bot:dev .` then run with
  `MOCK_BOT=1 BROWSER_IMAGE=mock-bot:dev`. Without it you get a real 502 on a missing on-demand
  image — correct behaviour, not a regression. (This is #722's territory.)
- **Buildkit contention is real.** Parallel worktree sessions building the same images corrupt the
  snapshot cache; `docker builder prune -f` recovers, but do not prune out from under another
  session's active build.
- **Hot files collide.** `core/meetings/modules/join/package.json`'s single-line `test` script
  collided between two PRs this cycle (resolved as a union). Sequence, or expect it.
