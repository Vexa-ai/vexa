# Test Rules

Agent MUST follow these. Every procedure imports them.

## 1. Procedures own their scripts

`.md` owns `.sh`. Script fails because reality changed → fix the script, retry, log FIX. The only valid FAIL is when the **software itself** is broken.

## 2. Graph owns all procedures

[graphs/ownership.md](graphs/ownership.md) owns every procedure. Wrong assumptions → fix the `.md`, fix the `.sh`, retry. Procedures converge toward truth through execution.

## 3. Never stop on fixable failure

1. Read the error
2. Diagnose (logs, inspect, env vars)
3. Fix script or procedure
4. Retry
5. Log FIX

Stop only when: human action required, ambiguous fix, or destructive fix.

## 4. Log everything

Every action → `test-log.md`. See `test-lib.sh` for format. What is not logged did not happen.

## 5. Failure modes grow

Every fix → append to that procedure's `Failure modes` table. The table is the procedure's memory.

## 6. Scripts are atomic

Each `.sh`: explicit args, sources `test-lib.sh`, outputs eval-able `KEY=value`, exits 0/1.

## 7. Tests own their feature docs

Each test declares `docs:` in its frontmatter and has a `## Docs ownership` section. The frontmatter lists WHICH docs. The section explains HOW — which specific claims this test proves, which fields to update, what to fix if reality differs.

**`docs:` lists the files. `## Docs ownership` lists the claims.**

After every test run, the test MUST follow its own `## Docs ownership` instructions:

1. Read each file listed in `docs:`
2. Check the specific claims listed in `## Docs ownership` against what the test observed
3. If reality differs → fix the doc now, log FIX
4. If the doc claims something the test couldn't prove → mark as untested
5. Update DoD tables: Status, Evidence, Last checked (ISO 8601 UTC: `YYYY-MM-DDTHH:MMZ`)
6. Update API examples: curl commands, response shapes, status codes, env var defaults

## 8. Diagnose before fix

Stop. Don't touch code. Read logs. Form hypothesis. Predict. Test prediction without changing code. THEN one fix per hypothesis. Verify. If confidence not rising after 3 cycles → escalate.

## 9. External observation

Bot self-reports are NOT evidence. Evidence hierarchy:
1. Screenshot/video of actual state
2. Database query
3. Log output
4. Self-reported status (lowest trust)

## 10. Signal specificity

A signal must be SPECIFIC to the condition, not just correlated. Ask: "What ELSE could cause this signal?" Combine positive signal + negative guard.

## 11. Test the system, not just the feature

Before reporting confidence ≥ 70: can the user load the dashboard? Does the API respond? Hit every health endpoint. `curl | head` proves server responds, not that the app works.

## 12. Only run owned scripts

Procedures are code. Execute them literally — run the `.sh`, follow the `.md` steps. Do not run arbitrary curl/psql/docker commands outside the procedures. If you need something that doesn't exist, create or update the owned script first, then run it. (2026-04-05: agent ran ad-hoc curl/psql commands, skipped steps, ran things in parallel that the graph defined as sequential.)

## 13. Use the API, not the database

The API is the system under test. Query transcripts via `GET /transcripts`, create users via `POST /admin/users`, check status via `GET /bots`. Direct Postgres queries bypass the code being tested and hide bugs. Exception: data capture for scoring (DB export after API verification).

## 14. Sequential means sequential

Test graphs define execution order. Steps with arrows between them are sequential — the output of one feeds the input of the next. Do not parallelize sequential steps. Do not skip ahead. Do not start S5 before S4 completes and you have verified its output. (2026-04-05: agent started scoring before collection was verified, ran GMeet and Teams "in parallel" when the graph showed them as sequential from S3.)
