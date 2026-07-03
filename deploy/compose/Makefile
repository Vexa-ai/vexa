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

.PHONY: stack-test stack-test-bot stack-test-mock dashboard-harness provision-token up down build ps logs bot _bot_check _access

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

## stack-test-bot — + the real bot-spawn proof (COMPOSE_BOT=1; slow, needs vexaai/vexa-bot:dev).
stack-test-bot:
	COMPOSE_BOT=1 ./bin/stack-test

## stack-test-stress — the control plane under CONCURRENT load (A:V2): N mock bots at once, no cap overspill.
## Needs mock-bot:dev. Tune STRESS_N / STRESS_CAP. Pass COMPOSE_PROJECT + MINIO_HOST_PORT to isolate on bbb.
stack-test-stress:
	COMPOSE_STRESS=1 MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test

## stack-test-chaos — the control plane RECOVERS from injected faults (A:V3): a redis/meeting-api blip
## (docker pause/unpause) mid-run → the FSM still reaches terminal. Needs mock-bot:dev.
stack-test-chaos:
	COMPOSE_CHAOS=1 MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test chaos_test.py

## stack-test-mock — the FULL control-plane lifecycle at L3 via the mock bot (A:V1). Needs mock-bot:dev
## (build: docker build -f ../../core/meetings/services/bot/Dockerfile.mock -t mock-bot:dev ../../). Runs
## the always-on subset PLUS the MOCK_BOT scenarios (normal/reject/crash/timeout/emit/stop/max-bots/
## speak/canAccess) — no browser/STT. Pass COMPOSE_PROJECT to isolate on a shared host (bbb).
stack-test-mock:
	MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test

## up / down / build — hand-drive the stack (dev only; the proof manages its own lifecycle).
## `up` is the zero-friction entrypoint: seeds .env from .env.example if absent, brings the stack up,
## then mints + prints an API key (via the idempotent bin/provision-token) with the URLs.
up:
	@[ -f .env ] || { echo "→ no .env found; seeding from .env.example"; cp .env.example .env; }
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory _bot_check
	@$(MAKE) --no-print-directory _access

## bot — build the meeting bot FROM SOURCE (the v0.12 bot that matches this stack's lifecycle.v1).
## The published vexaai/vexa-bot:dev on Docker Hub is the OLDER 0.10 line and is NOT compatible — the
## bot is built here, never pulled. Builds the join-env base first, then the bot, tagged BOT_IMAGE.
## Point BROWSER_IMAGE (.env) at BOT_IMAGE so the runtime spawns it.
bot:
	@echo "→ [1/2] join-env base: vexa/meet-join-env:dev"
	docker build -f $(REPO_ROOT)/core/meetings/modules/join/Dockerfile.env -t vexa/meet-join-env:dev $(REPO_ROOT)/core/meetings/modules/join
	@echo "→ [2/2] meeting bot: $(BOT_IMAGE)"
	docker build -t $(BOT_IMAGE) -f $(REPO_ROOT)/core/meetings/services/bot/Dockerfile $(REPO_ROOT)
	@echo "✓ built $(BOT_IMAGE) — ensure BROWSER_IMAGE=$(BOT_IMAGE) in .env, then: make all"

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
	 echo "═══════════════════════════════════════════════════════════"
build:
	$(COMPOSE) build
ps:
	$(COMPOSE) ps
logs:
	$(COMPOSE) logs -f
