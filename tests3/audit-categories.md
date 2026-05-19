# Audit categories — Vexa release-audit checklist

> Loaded by stage `audit` (see `tests3/stages/08-audit.md`). Each category
> is a checklist of bad patterns to look for in the cycle's diff. Categories
> evolve by appending: every retro that finds a class of bug we missed
> graduates the pattern into here.

> Status: SCAFFOLD landed in v0.10.5.3. Full audit-stage wiring (Makefile
> target, stage.py TRANSITIONS, skill) lands in v0.10.6.

---

## How to read this file

Each category has:
- **Why it's here** — a concrete recent bite (release tag + impact). If a
  category lacks this, it doesn't belong yet — get cut.
- **Patterns to grep** — exact code patterns that are bad. The static
  layer scripts each map to ≥1 of these.
- **Default severity** — what severity a match gets unless context downgrades.

Severity scale (from `tests3/stages/08-audit.md`):
- **BLOCKER** — must fix before ship; data-loss / security-exposure class.
- **CRITICAL** — must fix this cycle; resilience anti-pattern in critical path.
- **MAJOR** — file followup, can ship.
- **MINOR** — hygiene; track only.

---

## 1. Security gaps

**Why:** v0.10.5.3 audit found `services/api-gateway/main.py:2057` —
public bot-callback proxy added without `require_auth` or env gate (CRITICAL).
Public exposure of internal callback paths can be driven by anyone with a
session_uid (UUIDv4) to mutate meeting state arbitrarily.

**Patterns to grep:**
- `require_auth=False` on a route that handles writes (POST/PUT/DELETE)
  without an environment gate (`if VEXA_ENV != "production"`) AND without a
  shared-secret header check.
- `f".*\{[a-z_]+\}.*"` inside `execute(...)` / `text(...)` SQL builders
  → likely SQL injection.
- `Access-Control-Allow-Origin: *` in any new file or response builder.
- `cors.*allow_origins.*\["\*"\]` in FastAPI / Express config.
- Hardcoded values matching `vxa_[a-z]+_[A-Za-z0-9]{32,}` (Vexa API
  token format) in code or yaml.
- Hardcoded passwords / shared secrets — patterns like
  `password\s*=\s*"[^"]+"` , `secret\s*=\s*"[^$"]` .
- Any new route under `/internal/` accessible from `api-gateway` without
  auth proxy. (`/internal/` is by convention docker-network-only.)
- Webhook envelope construction that includes fields from an internal-only
  source — look for `"webhook_secret"`, `"_*"` fields, or fields not in
  the public webhook schema.
- Path traversal in storage paths — `os.path.join(base, user_input)`
  without `_normalize_path` defense.
- URL fetches without an allow-list — `httpx.get(user_provided_url)` is
  SSRF.

**Default severity:** CRITICAL or BLOCKER depending on whether the
exposed path can leak user data (BLOCKER) or just probe state (CRITICAL).

---

## 2. Undocumented fallbacks

**Why:** v0.10.5.2 shipped Pack M's chunk-buffer leak — the original code
had a "fallback for shutdown-flush" comment buffering every chunk for the
meeting lifetime, never trimmed. 24-min crash class. Pack P (in v0.10.5.3)
codified the rule: "no fallbacks unless explicitly decided with the human."

**Patterns to grep:**
- `try:.*except.*:\s*pass` (silent swallow with no error context).
- `try:.*except.*:\s*return None` (default-to-null fallback).
- `try:.*except.*:\s*return \[\]` / `return \{\}` (default-to-empty).
- `if .*: .* else: # fallback` patterns where the fallback branch lacks
  an explicit `# DECISION: <name>` comment naming what was decided.
- New code matching the regex `(?i)fallback` without a co-located decision
  comment (e.g. `# fallback path: <reason>` or `# Pack <X> decision: <ref>`).
- Comments like `# in case X fails` / `# just in case` / `# safety` on
  branches with no test coverage.

**Default severity:** CRITICAL if the fallback is in a critical path
(audio/video/auth/billing); MAJOR otherwise.

**Existing enforcement:** `tests3/tests/v0.10.5.3-no-fallbacks-pii.sh`
catches the `fallback` keyword in the bot codebase (warns at validate
stage). Audit extends this to ALL new code in the diff.

---

## 3. Resilience invariants

**Why:** Pack M (v0.10.5.3) — `__vexaRecordedChunks: Blob[]` array with no
cap. Pack G's logBuffer — same class, found and fixed in same cycle.
Recurring pattern of in-memory state structures growing without bound.

**Patterns to grep:**
- `[].push(` / `Array.append` / `list.append` / `dict[k] = v` in long-lived
  state without a corresponding `if len(...) > CAP: ...shift()` defense.
- `httpx.get(...)` / `httpx.post(...)` / `requests.get(...)` /
  `aiohttp.ClientSession()` without `timeout=` argument. (Default httpx
  timeout is 5s connect + UNLIMITED read — a stuck server hangs the caller
  forever.)
- `redis.Redis(...)` without `socket_timeout` and `socket_connect_timeout`.
- `await psycopg.connect(...)` / `asyncpg.connect(...)` without `timeout=`.
- DB queries without `LIMIT` on tables that grow unboundedly (events,
  audit logs, transcript segments). Look for `select(Table).where(...)`
  without `.limit(...)` in repository code.
- `asyncio.create_task(...)` without storing the reference (task gets GC'd).
- Sync `requests.get(...)` / `time.sleep(...)` inside `async def` —
  blocks the event loop.

**Default severity:** CRITICAL if the path is on every request (e.g. main
event loop, webhook delivery); MAJOR if confined to bounded operations.

---

## 4. Flaky complex workarounds

**Why:** v0.10.5.3 cycle — `containers.sh` `status_completed` test polled
24×5s with hardcoded sleep, raced against meeting-api startup-DNS-race
restarts. Failed iter-1 (RED), passed iter-2 (deterministic). Two cycles
of triage time spent on a fixture that should have been wait-on-condition.

**Patterns to grep:**
- `time.sleep(N)` / `await asyncio.sleep(N)` followed by a state check —
  the sleep is a workaround for not having proper wait-on-condition.
  Exception: deliberate rate-limiting (annotate with `# rate-limit:
  N requests/sec`).
- `for i in range(N): if cond: break; sleep(M)` — polling loop that
  should be `await wait_for(cond, timeout=...)`.
- Retry loops that hide an upstream bug rather than fix it (3+ retries
  on a code path that should succeed first try).
- "Just" comments — `# just retry` / `# just kill it and restart` /
  `# we'll figure out why later`.
- Workarounds nested inside workarounds (depth 2+ of "if X then Y else
  if Y' then Z").

**Default severity:** MAJOR. Upgrade to CRITICAL if the workaround masks a
data-loss class or a cascade-failure trigger.

---

## 5. Industry anti-patterns

**Why:** Hand-rolled crypto / auth / retry-with-backoff has bitten teams
across the industry forever. The vexa codebase has so far avoided most;
the audit is to prevent regression.

**Patterns to grep:**
- Custom hash construction — `hashlib.sha256(secret + payload).hexdigest()`
  is HMAC if not done as `hmac.new(secret, payload, sha256).hexdigest()`
  — vulnerable to length-extension. Use `hmac.compare_digest` for
  comparisons.
- `random.choice(...)` for security-sensitive operations (use
  `secrets.choice`).
- `JSON.stringify` for canonical signing without explicit key sorting —
  signature differs between Python and JS implementations.
- Hand-rolled connection pools (any class implementing `_get_connection`
  / `_release_connection` over a primitive — use `redis-py` /
  `asyncpg.Pool` / similar).
- Hand-rolled retry-with-backoff (use `tenacity` / `backoff` library).
- Hand-rolled config parsing — should use `pydantic-settings`.

**Default severity:** MAJOR (refactor to library), CRITICAL if security-
adjacent (hand-rolled crypto/auth).

---

## 6. Operational hygiene

**Why:** v0.10.5.x cycle — `cmd 2>&1 | tail` exit-code masking pattern
masked a docker build failure that would have been caught immediately. Also:
`VEXA_BYPASS_STAGE=1` escape valve was used during emergency hotfixes,
documented in retro as a discipline failure.

**Patterns to grep:**
- Shell pipelines `cmd | tail` / `cmd | head` / `cmd | jq` without
  `set -o pipefail` earlier in the script (or in CI, without `pipefail`
  in shell setup). The chain's exit code is the LAST command's, masking
  upstream failures.
- `VEXA_BYPASS_STAGE=1` references in NEW code (not in escape-valve
  documentation).
- `--no-verify` / `--force` / `--no-gpg-sign` in any CI yaml or makefile
  recipe added this cycle.
- `# TODO` / `# FIXME` / `# XXX` comments without a `#NNN` GH issue
  reference on the same or next line.
- `curl -sSf https://...` followed by `| bash` — supply-chain attack
  vector.
- Inline `password` / `token` env vars set in shell scripts (not from
  `.env` or secrets manager).

**Default severity:** MAJOR. Upgrade to CRITICAL if the hygiene gap
materially impacts what we'd see in CI / production logs.

---

## 7. PII / data discipline

**Why:** v0.10.5.2 retro found 5 customer real names + emails leaked into
public OSS commits, README, release notes. Option B redaction (post-hoc
scrub via `gh api PATCH`) was used instead of force-push history rewrite.
Discipline: anonymize at write-time using `customer-A`, `customer-B`,
... pattern.

**Patterns to grep:**
- Any string in `tests3/releases/<id>/`, `RELEASE_NOTES.md`,
  `features/*/README.md`, or PR descriptions matching:
  - `[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}` (real-looking email) AND
    NOT one of `team@vexa.ai`, `noreply@*`, `*@redacted`, `<email>@<domain>`,
    `customer-[A-Z]@vexa-platform`.
  - Capitalized first/last name patterns common to real people (e.g.
    "Brian Jensen", "Jeroen Nas", "Christian Zacher-Gross") — NOT
    matching `customer-[A-Z]`, `contributor-[0-9]`, etc.
- Internal infra IPs / FQDNs in OSS files — `dashboard.vexa.ai`,
  `transcription-service.dev.vexa.ai`, `vexa-platform.com`. (Test
  cluster IPs like `172.x.x.x` are fine — those are ephemeral.)
- Customer meeting URLs / native IDs that are real (e.g.
  `teams.microsoft.com/meet/371508896118668?p=...` was the real URL we
  reproduced #281 on — anonymize).

**Default severity:** BLOCKER (real PII in OSS) or MAJOR (internal FQDN
in OSS but not customer-identifying).

**Existing enforcement:** `tests3/tests/v0.10.5.3-no-fallbacks-pii.sh`
catches this in `tests3/releases/`. Audit extends to all OSS files.

---

## 8. Observability gaps

**Why:** Pack G.1 / Pack G.2 / Pack O of v0.10.5.x — bot stdout was
entirely unstructured before structured-JSON logger landed. v0.10.5.2
forensic blindness was the root cause of "couldn't tell what crashed
Brian Jensen's bot at 24 min" — meeting only had `print()` lines.

**Patterns to grep:**
- `print(` / `console.log(` in service code (bot, meeting-api,
  runtime-api, dashboard server) — should be `logJSON` / `logger.info`
  with structured fields.
- New log lines without correlation IDs (no `meeting_id` / `session_uid`
  / `request_id` in the structured fields).
- Critical-path operations with no log line at all (entry / exit / error).
  E.g. a new function that does network I/O but logs nothing.
- Text-only log strings — `logger.info(f"foo {x}")` — should be
  `logger.info("foo", extra={"x": x})` for filterable structured logs.

**Default severity:** MAJOR. Upgrade to CRITICAL if the gap is on a
forensic-critical path (bot exit, meeting failure, billing event).

---

## 9. Test integrity

**Why:** Skipped/rotted tests give false confidence. Mock-everything tests
exercise no real I/O. Sleep-based tests are flaky.

**Patterns to grep:**
- `assert True` / `expect(true).toBe(true)` patterns in new tests.
- Tests with `@pytest.mark.skip` without a `# tracked: <gh-issue>`
  comment.
- Tests with `time.sleep(N)` instead of waits-on-condition
  (`asyncio.wait_for`, `pytest_asyncio.timeout`).
- Tests that mock so much they exercise no real I/O — heuristic:
  `mock.patch` count > 5 in a single test function.
- Tests that depend on order — `pytest -p no:randomly` ordering, or
  fixtures that mutate shared state.

**Default severity:** MAJOR (file followup). Upgrade to CRITICAL if the
test is the ONLY guard for a customer-impact path.

---

## Adding a new category

When a retro finds a class of bug not caught by the existing categories:

1. Append a new section here with:
   - `## N. <category name>` header
   - **Why:** concrete release tag + impact (one sentence)
   - **Patterns to grep:** specific bad patterns with examples
   - **Default severity:** with calibration
2. If the patterns are mechanical (greppable), add a static-layer script
   to `tests3/audit/patterns/<category>.sh`.
3. Update `tests3/stages/08-audit.md`'s steps if the category needs
   contextual review (not just static).
4. Reference the category in the next release's audit-findings.md.

Categories with zero matches across 3 consecutive cycles can be archived
to `tests3/audit-categories-archived.md` to keep the active list focused.
