---
id: test/docs-compose
type: validation
requires: []
produces: [GATEWAY_URL, ADMIN_URL, DASH_URL]
validates: [documentation, infrastructure]
docs: [deploy/compose/README.md, deploy/compose/docker-compose.yml, deploy/env/env-example, deploy/compose/Makefile]
mode: manual
---

# Docs vs Compose — Follow the README

> This test has one rule: **open the README and do exactly what it says.**
> Every command, every claim, every table — verify it works as documented.
> When reality disagrees with the doc, that's a finding. Fix the doc or fix the code.

Source of truth: [deploy/compose/README.md](../deploy/compose/README.md)

---

## Phase 0 — Coverage gate (test vs README)

Before running anything, verify this test file covers every testable section of the README.
Walk the README top-to-bottom. For each section, confirm a test section exists below.
Any uncovered README section is a gap — add a test section before proceeding.

| README section | Lines | Test section | Covered? |
|---|---|---|---|
| **Why** | 3–5 | — | N/A (informational) |
| **What** — active services list | 9–20 | S2 | YES |
| **What** — experimental services note | 20 | S2 | YES |
| **What** — Docker socket claim | 19 | S2 | YES |
| **What** — transcription link | 22 | S15 | YES |
| **Quick start** — `make all` | 24–31 | S1 | YES |
| **Quick start** — skip build (pre-built images) | 33–39 | S11 | YES |
| **Quick start** — "Before running" (.env edit) | 41–43 | S1 | YES |
| **Make targets** table (15 targets) | 47–65 | S4 | YES |
| **Image tagging** — VERSION-YYMMDD-HHMM | 67–83 | S12 | YES |
| **Configuration** — Required table | 90–96 | S5 | YES |
| **Configuration** — Optional table (7 vars) | 100–112 | S5 | YES |
| **Configuration** — "Full env reference" link | 114 | S15 | YES |
| **External database** | 116–125 | S8 | YES |
| **Local GPU transcription** | 127–133 | S9 | YES |
| **Files** table | 137–143 | S14 | YES |
| **Service ports (internal)** table | 149–162 | S3 | YES |
| **Startup dependency order** | 165–171 | S6 | YES |
| **Upgrading from pre-0.10** | 173–188 | S10 | YES |
| **Cleanup** | 190–195 | S7 | YES |
| **Security** | 197–201 | S16 | YES |
| **Definition of Done** table | 203–228 | S17 | YES |

**Gate rule:** if any row shows "NO", stop and add the missing test section first.

---

## Phase 1 — Execute each section

### S1. Quick start

Stop any running stack and clear the build tag.

```bash
make down 2>/dev/null; rm -f deploy/compose/.last-tag
```

**S1a. Upgrade path (`.env` exists):**

1. Run `make all`
2. Verify `make env` says ".env patched: N new variable(s)" or ".env is up to date" — never overwrites existing values
3. Verify `init-db` runs idempotent schema sync (safe on existing data)
4. Verify `setup-api-key` skips if `VEXA_API_KEY` is already set
5. Verify `make test` passes

**S1b. Fresh clone path (no `.env`):**

> **Destructive step** — only run this if you can afford to lose your current `.env`.
> Back up first: `cp .env .env.backup`

```bash
rm -f .env
```

1. Run `make all`
2. Verify `make env` creates `.env` from env-example
3. Edit `.env` — set `TRANSCRIPTION_SERVICE_URL`
4. Verify the full chain completes: env → build → up → init-db → setup-api-key → test
5. Verify the final output shows all services healthy
6. Restore your config: `cp .env.backup .env`

### S2. Verify "What" claims

The README lists which services run. Compare against reality:

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.yml ps
```

- Every service the README lists as running must be running
- Services marked *(experimental)* in README must be commented out in compose — verify they are NOT running
- Any commented-out service in compose must not be listed as active in README
- "Bots spawn as Docker containers (needs Docker socket)" — verify `/var/run/docker.sock` is mounted

### S3. Walk the port table

The README has a "Service ports (internal)" table. For **each row**:

1. Read the documented port
2. Read the documented health/verify command
3. Run the health command exactly as written
4. Verify the port matches the default in `docker-compose.yml` and `env-example`
5. Skip rows marked *(experimental)* — verify those services are NOT running instead

Every mismatch is a finding. Three-way disagreement (README vs compose vs env-example) is a critical finding.

### S4. Try each Make target

The README has a "Make targets" table. For each row:

1. Verify the target exists in the Makefile
2. Run each non-destructive target (`ps`, `logs`, `test`, `help-tags`, `init-db`)
3. Verify the description matches what it actually does
4. Reverse check: every `.PHONY` target in the Makefile should be documented

### S5. Check configuration

The README documents required and optional env vars.

**Required vars:** verify each exists in `env-example` with a placeholder value.

**Optional vars:** for each row in the optional table, verify the documented default matches:
- The value in `env-example`
- The `${VAR:-default}` fallback in `docker-compose.yml`

Three-way agreement required. Any disagreement is a finding.

### S6. Verify dependency order

The README documents a startup order. Verify `depends_on` in `docker-compose.yml` enforces it:

- Infra (postgres, redis, minio) has no dependencies
- Foundation services (admin-api, runtime-api) depend on postgres
- Dependent services (meeting-api, api-gateway, mcp, tts-service) depend on foundation
- Frontend (dashboard) depends on api-gateway

### S7. Cleanup

Execute the README's cleanup instructions exactly as written:

```bash
make down && docker compose ps  # should be empty
```

Verify no containers remain.

### S8. REMOTE_DB path

The README documents an "External database" section. Verify:

1. Every var shown in the code block (`REMOTE_DB`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) exists in `env-example`
2. `init-db` in the Makefile checks `REMOTE_DB` and skips the postgres container check when it's `true`
3. Services that use the DB (`admin-api`, `meeting-api`) default `DB_HOST` to `postgres` in compose

### S9. LOCAL_TRANSCRIPTION path

The README documents a "Local GPU transcription" section. Verify:

1. `LOCAL_TRANSCRIPTION` exists in `env-example` (commented out)
2. `make up` in the Makefile checks `LOCAL_TRANSCRIPTION=true` and starts `services/transcription-service/`
3. `make down` also stops `services/transcription-service/`
4. `services/transcription-service/` has a `docker-compose*.yml` and a `README.md`
5. The README self-host links point to `services/transcription-service/README.md` and the file exists

### S10. Schema migration (pre-0.10 upgrade)

The README has an "Upgrading from pre-0.10" section. Verify:

1. `init-db` in the Makefile runs schema sync on **both** `admin_models.database.init_db` and `meeting_api.database.init_db`
2. Every column listed in the upgrade table exists in the model source code:
   - Check `libs/admin-models/admin_models/models.py` for api_tokens columns
   - Check `services/meeting-api/meeting_api/models.py` for transcriptions columns
3. Run `make init-db` against a database with existing data — verify it adds missing columns without dropping data

### S11. Pre-built images (skip build)

The README documents skipping the build with `IMAGE_TAG=dev make up`. Verify:

1. `IMAGE_TAG=dev make up` pulls and starts services without a local build
2. `make init-db` works
3. `make setup-api-key` works
4. `make test` passes

### S12. Image tagging

The README documents the tagging workflow. Verify:

1. `make build` writes a tag to `deploy/compose/.last-tag`
2. `make up` reads from `.last-tag`
3. The tag format matches `VERSION-YYMMDD-HHMM` (check `IMAGE_TAG` definition in Makefile — includes VERSION prefix)
4. `IMAGE_TAG=custom make build` overrides the default
5. The example in the README (`0.10.0-260330-1415`) matches the actual format produced

### S13. restore-db

The README documents `make restore-db`. Verify:

1. The target exists in the Makefile
2. Running without `DUMP=` shows usage
3. The restore script at `tests/scripts/restore-prod-dump.sh` exists and is executable

### S14. Files table

The README has a "Files" table listing key files. Verify:

1. `docker-compose.yml` exists at `deploy/compose/docker-compose.yml`
2. `Makefile` exists at `deploy/compose/Makefile`
3. The descriptions match what the files actually contain

### S15. Links

Every link in the README must resolve to an existing file. Verify:

1. `../../services/transcription-service/README.md` exists (referenced twice — "What" section and "Configuration > Required")
2. `../env/env-example` exists
3. `../env/README.md` exists ("Full env reference" link)
4. `../lite/README.md` exists ("Vexa Lite" link in "Why")
5. `../../tests/00-docs-compose.md` exists ("Definition of Done" link)

### S16. Security claims

The README has a "Security" section. Verify:

1. "Never log secrets" — check that `setup-api-key` and `init-db` in the Makefile do NOT echo `ADMIN_TOKEN`, `DB_PASSWORD`, or `VEXA_API_KEY` values. They may log that a value is set, but not the value itself.
2. "Create test users/meetings per run" — verify `setup-api-key` creates a new user/key rather than reusing hardcoded credentials

### S17. Definition of Done cross-references

The README has a "Definition of Done" table that references test sections. Verify:

1. Every `S<N>` reference in the table corresponds to a section in this file
2. Every test section in this file is referenced by at least one row in the table
3. Weights sum to 100

---

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Admin API unreachable on 8057 | compose default was 8067, README said 8057 | Changed compose + env-example + Makefile to 8057 | Ports drift when files are edited independently |
| Port table lists commented-out service | Service removed from compose, README not updated | Marked experimental in README | README and compose must be edited together |
| Health command in table doesn't work | Endpoint changed | Fix the table | Health endpoints change — table must be re-verified |
| init-db doesn't add admin columns | Only ran meeting-api sync, not admin-api | Added admin-api sync to init-db | Prerequisites need full sync too |
| ADMIN_TOKEN default disagreement | compose fallback was `vexa-admin-token`, README/env said `changeme` | Aligned compose to `changeme` | Three-way defaults must be checked on every change |
| Makefile test used wrong port fallback | `ADMIN_PORT:-8067` hardcoded in test target | Fixed to `ADMIN_PORT:-8057` | Fallback defaults in Makefile must match env-example |
| Tag format wrong in README | README said `YYMMDD-HHMM`, Makefile produces `VERSION-YYMMDD-HHMM` | Updated README + example | Check Makefile IMAGE_TAG definition, not assumptions |
| BOT_IMAGE_NAME default mismatch | README showed `${IMAGE_TAG}`, env-example had `:dev` | Aligned README to `:dev` | README optional table must match env-example literally |
| Agent API listed as active | Commented out in compose (NO-SHIP 0.10) | Removed from active list, marked experimental | Commented-out services must not appear as active |
| `LOCAL_TRANSCRIPTION=true` fails | `services/transcription-service/` had no base `docker-compose.yml` — only `.cpu.yml` and `.override.yml` | Created GPU base `docker-compose.yml` with 2 workers matching nginx.conf and override | Makefile `docker compose up -d` needs a default compose file to exist |
| DoD table incomplete | Only covered 13 of 17 test sections, weights summed to 105 | Added rows for S6, S7, S14–S17; rebalanced to 100 | DoD table must be updated when test sections are added |

## Docs ownership

After this procedure completes, update in [deploy/compose/README.md](../deploy/compose/README.md):

- "What" section: must list only active services; experimental noted separately
- Port table: every row matches compose and is reachable (experimental rows marked)
- Make targets table: every target exists and description is accurate
- Configuration defaults: three-way agreement (README, compose, env-example)
- External DB: REMOTE_DB + all DB vars documented
- Local transcription: LOCAL_TRANSCRIPTION documented, compose file exists
- Schema sync: init-db runs both admin + meeting syncs
- Dependency order: matches compose `depends_on`
- Files table: both files exist and descriptions accurate
- Links: every internal link resolves
- Security: no secrets logged
- Definition of Done: update Status, Evidence, Last checked
- Confidence score: recalculate based on results
