# deploy/compose — the v0.12 control-plane stack (P4) + its readiness proof (P5, gate:compose).
#
# The stack-readiness proof (tests/stack_test.py) owns the full up→prove→down lifecycle in its
# session fixture, so `make stack-test` is the one entrypoint the gate calls. The bare up/down
# targets below are for hand-driving the stack during development.
PROJECT ?= vexa-v012
BROWSER_IMAGE ?= mock-bot:dev
EMAIL ?= self-host@vexa.ai
SCOPES ?= bot,tx
COMPOSE  = docker compose -p $(PROJECT) -f docker-compose.yml
# The meeting bot is built FROM SOURCE (matching this stack's lifecycle.v1), never pulled — see `bot`.
REPO_ROOT := $(abspath ../..)
BOT_IMAGE ?= vexaai/vexa-bot:v012

.PHONY: stack-test stack-test-bot stack-test-mock stack-test-v010 dashboard-harness provision-token up down build ps logs bot _preflight _bot_check _creds_check _access

## provision-token — mint (idempotently) the self-host dashboard's API token (compose bring-up step 2).
## Needs ADMIN_TOKEN (the stack admin secret). Prints ONLY the token, so it pipes into the deploy:
##   DASHBOARD_API_KEY=$$(make -s provision-token ADMIN_TOKEN=<secret>) \
##     docker compose -f docker-compose.yml -f docker-compose.dashboard.yml up -d dashboard
provision-token:
	@ADMIN_TOKEN="$(ADMIN_TOKEN)" EMAIL="$(EMAIL)" SCOPES="$(SCOPES)" ./bin/provision-token

## dashboard-harness — the dashboard FULL-SURFACE proof: send-bot + live WS transcript through the
## vendored dashboard, against the real stack + the mock bot (deterministic, no browser/STT/meeting).
## Needs mock-bot:dev + vexa-dashboard:dev (docker build -f clients/dashboard/Dockerfile --build-arg
## VEXA_API_URL=http://gateway:8000 -t vexa-dashboard:dev ../.. from the repo root).
dashboard-harness:
	./bin/dashboard-harness.sh

## stack-test — the always-on gate:compose proof (brings the stack up + down itself).
stack-test:
	./bin/stack-test

## stack-test-bot — + the real bot-spawn proof (COMPOSE_BOT=1; slow, needs vexaai/vexa-bot:v012).
stack-test-bot:
	COMPOSE_BOT=1 ./bin/stack-test

## stack-test-stress — the control plane under CONCURRENT load (A:V2): N mock bots at once, no cap overspill.
## Needs mock-bot:dev. Tune STRESS_N / STRESS_CAP. Pass COMPOSE_PROJECT + MINIO_HOST_PORT to isolate on a shared dev host.
stack-test-stress:
	COMPOSE_STRESS=1 MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test

## stack-test-chaos — the control plane RECOVERS from injected faults (A:V3): a redis/meeting-api blip
## (docker pause/unpause) mid-run → the FSM still reaches terminal. Needs mock-bot:dev.
stack-test-chaos:
	COMPOSE_CHAOS=1 MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test chaos_test.py

## stack-test-mock — the FULL control-plane lifecycle at L3 via the mock bot (A:V1). Needs mock-bot:dev
## (build: docker build -f ../../core/meetings/services/bot/Dockerfile.mock -t mock-bot:dev ../../). Runs
## the always-on subset PLUS the MOCK_BOT scenarios (normal/reject/crash/timeout/emit/stop/max-bots/
## speak/canAccess) — no browser/STT. Pass COMPOSE_PROJECT to isolate on a shared host.
stack-test-mock:
	MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test

## stack-test-v010 — the 0.10 API backward-compat behavioral suite (release DoD 6): a 0.10-style
## client's real REST + WS flows against the running 0.12 stack, MOCK_BOT scenarios only. Opt-in
## via V010_COMPAT=1 (without it the compat dir is not even collected — the default gate:compose
## collection is unchanged). Needs mock-bot:dev, like stack-test-mock. Runs ONLY tests/compat/.
stack-test-v010:
	V010_COMPAT=1 MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test compat

## up / down / build — hand-drive the stack (dev only; the proof manages its own lifecycle).
## `up` is the zero-friction entrypoint: seeds .env from .env.example if absent, brings the stack up,
## then mints + prints an API key (via the idempotent bin/provision-token) with the URLs.
up:
	@$(MAKE) --no-print-directory _preflight
	@[ -f .env ] || { echo "→ no .env found; seeding from .env.example"; cp .env.example .env; }
	$(COMPOSE) up -d --build
	$(COMPOSE) build agent-worker  # profiles:[build-only] keeps it out of `up` — build it explicitly or first chat fails to spawn
	@$(MAKE) --no-print-directory _bot_check
	@$(MAKE) --no-print-directory _creds_check
	@$(MAKE) --no-print-directory _access

## _preflight — fail fast on a too-old Docker engine: agent workers mount workspaces via the Mounts
## API's named-volume Subpath, which needs engine ≥ v26 — an older engine fails every worker create
## AFTER the stack looks healthy, which is far harder to diagnose than failing here.
_preflight:
	@v=$$(docker version --format '{{.Server.Version}}' 2>/dev/null); v=$${v#v}; \
	 if [ -z "$$v" ]; then echo "✗ cannot reach the Docker engine — is Docker running?"; exit 1; fi; \
	 major=$${v%%.*}; \
	 case "$$major" in \
	   ''|*[!0-9]*) echo "⚠ unrecognized Docker engine version '$$v' — continuing (this stack needs ≥ v26)";; \
	   *) if [ "$$major" -lt 26 ]; then \
	        echo ""; \
	        echo "✗ Docker engine v$$v is too old — this stack requires engine ≥ v26."; \
	        echo "  Agent workers mount workspaces with named-volume subpaths (Mounts API"; \
	        echo "  VolumeOptions.Subpath); older engines reject the container create."; \
	        echo "  Upgrade Docker, then re-run: make all"; \
	        exit 1; \
	      fi;; \
	 esac

## bot — build the meeting bot FROM SOURCE (the v0.12 bot that matches this stack's lifecycle.v1).
## The published vexaai/vexa-bot:dev on Docker Hub is the OLDER 0.10 line and is NOT compatible — the
## bot is built here, never pulled. Builds the join-env base first, then the bot, tagged BOT_IMAGE.
## Point BROWSER_IMAGE (.env) at BOT_IMAGE so the runtime spawns it.
bot:
	@echo "→ [1/2] join-env base: vexa/meet-join-env:dev"
	docker build -f $(REPO_ROOT)/core/meetings/modules/join/Dockerfile.env -t vexa/meet-join-env:dev $(REPO_ROOT)/core/meetings/modules/join
	@echo "→ [2/2] meeting bot: $(BOT_IMAGE)"
	docker build -t $(BOT_IMAGE) -f $(REPO_ROOT)/core/meetings/services/bot/Dockerfile $(REPO_ROOT)
	@_rt=$$($(COMPOSE) ps -q runtime 2>/dev/null); _live=""; \
	 [ -n "$$_rt" ] && _live=$$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $$_rt 2>/dev/null \
	   | grep '^BROWSER_IMAGE=' | cut -d= -f2-); \
	 if [ "$$_live" = "$(BOT_IMAGE)" ]; then \
	   echo "✓ built $(BOT_IMAGE) — done. The running stack spawns bots from this tag;"; \
	   echo "  the next bot will use the image you just built (no restart needed)."; \
	 else \
	   echo "✓ built $(BOT_IMAGE) — set BROWSER_IMAGE=$(BOT_IMAGE) in .env, then: make all"; \
	 fi

## _bot_check — FAIL LOUD if the meeting bot image isn't built locally. The bot is built here, never
## pulled (the published vexaai/vexa-bot:dev is the old 0.10 line). Without this the failure was silent:
## bots sat in `requested` and the meeting was reaped to `completed`/0-segments. Now it is screamingly visible.
_bot_check:
	@_bi=$$(grep -E '^BROWSER_IMAGE=' .env 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$$//; s/[[:space:]]+$$//'); \
	 if [ -n "$$_bi" ] && ! docker image inspect "$$_bi" >/dev/null 2>&1; then \
	   echo ""; \
	   echo "═══════════════════════════════════════════════════════════"; \
	   echo "  ⚠️  meeting bot image '$$_bi' is NOT built locally."; \
	   echo "      The bot is built FROM SOURCE here, never pulled (the published"; \
	   echo "      vexaai/vexa-bot:dev is the old 0.10 line, incompatible with this stack)."; \
	   echo "      Bots will FAIL TO JOIN until you build it:"; \
	   echo "          make -C deploy/compose bot"; \
	   echo "═══════════════════════════════════════════════════════════"; \
	   echo ""; \
	 fi

## _creds_check — WARN (never fail) when .env leaves a capability unconfigured: empty STT creds mean
## bots join + record but produce NO transcript; no model credential means agent chat refuses at first
## use. Mirrors agent-api's config.v1 `model_inference` capability (mode=any: any ONE key configures it).
_creds_check:
	@_envv() { grep -E "^$$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$$//; s/[[:space:]]+$$//'; }; \
	 stt=1; [ -n "$$(_envv TRANSCRIPTION_SERVICE_URL)" ] && [ -n "$$(_envv TRANSCRIPTION_SERVICE_TOKEN)" ] && stt=0; \
	 model=1; for k in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN VEXA_LLM_API_KEY HOST_CLAUDE_CREDENTIALS; do \
	   [ -n "$$(_envv $$k)" ] && model=0; done; \
	 if [ $$stt -eq 1 ] || [ $$model -eq 1 ]; then \
	   echo ""; \
	   echo "═══════════════════════════════════════════════════════════"; \
	   if [ $$stt -eq 1 ]; then \
	     echo "  ⚠️  transcription is NOT configured (TRANSCRIPTION_SERVICE_URL /"; \
	     echo "      TRANSCRIPTION_SERVICE_TOKEN empty in .env)."; \
	     echo "      Bots will join and record but produce NO transcript."; \
	     echo "      Get a free token at https://vexa.ai/account (or self-host"; \
	     echo "      deploy/transcription), set both, then: make all"; \
	   fi; \
	   if [ $$stt -eq 1 ] && [ $$model -eq 1 ]; then echo "  ─────────────────────────────────────────────────────────"; fi; \
	   if [ $$model -eq 1 ]; then \
	     echo "  ⚠️  no model credential is configured — agent chat will refuse to run."; \
	     echo "      Set ONE of CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY,"; \
	     echo "      ANTHROPIC_AUTH_TOKEN, VEXA_LLM_API_KEY or HOST_CLAUDE_CREDENTIALS"; \
	     echo "      in .env, then: make all — or configure Settings → Models in the UI."; \
	   fi; \
	   echo "═══════════════════════════════════════════════════════════"; \
	   echo ""; \
	 fi
down:
	$(COMPOSE) down -v --remove-orphans

## _access — wait for admin-api, mint the self-host API key, print it + the URLs (used by `up`).
_access:
	@_envv() { grep -E "^$$1=" .env | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$$//; s/[[:space:]]+$$//'; }; \
	 ADMIN_TOKEN=$$(_envv ADMIN_TOKEN); \
	 APORT=$$(_envv ADMIN_API_PORT); APORT=$${APORT:-18057}; \
	 GW=$$(_envv API_GATEWAY_HOST_PORT); GW=$${GW:-18056}; \
	 UI=$$(_envv TERMINAL_PORT); UI=$${UI:-13000}; \
	 echo "⏳ minting your self-host API key..."; \
	 KEY=""; for i in $$(seq 1 60); do \
	   KEY=$$(ADMIN_TOKEN=$$ADMIN_TOKEN ADMIN_API_URL=http://127.0.0.1:$$APORT EMAIL=$(EMAIL) SCOPES=$(SCOPES) ./bin/provision-token 2>/dev/null) && [ -n "$$KEY" ] && break; \
	   sleep 2; done; \
	 echo ""; \
	 echo "═══════════════════════════════════════════════════════════"; \
	 echo "  Vexa is up."; \
	 echo "    Terminal UI : http://localhost:$$UI"; \
	 echo "    API gateway : http://localhost:$$GW"; \
	 if [ -n "$$KEY" ]; then echo "    API key     : $$KEY"; \
	 else echo "    API key     : admin-api not ready — mint later with:"; \
	      echo "                  make -s provision-token ADMIN_TOKEN=<your ADMIN_TOKEN>"; fi; \
	 STT=1; [ -n "$$(_envv TRANSCRIPTION_SERVICE_URL)" ] && [ -n "$$(_envv TRANSCRIPTION_SERVICE_TOKEN)" ] && STT=0; \
	 MODEL=1; for k in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN VEXA_LLM_API_KEY HOST_CLAUDE_CREDENTIALS; do \
	   [ -n "$$(_envv $$k)" ] && MODEL=0; done; \
	 if [ $$STT -eq 1 ]; then echo "    ⚠ transcription: not configured — bots record, NO transcript"; fi; \
	 if [ $$MODEL -eq 1 ]; then echo "    ⚠ agent chat  : no model credential — chat will refuse"; fi; \
	 echo "═══════════════════════════════════════════════════════════"
build:
	$(COMPOSE) build
ps:
	$(COMPOSE) ps
logs:
	$(COMPOSE) logs -f
