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

.PHONY: stack-test stack-test-bot stack-test-mock dashboard-harness provision-token up down build ps logs _access

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
	@$(MAKE) --no-print-directory _access
down:
	$(COMPOSE) down -v --remove-orphans

## _access — wait for admin-api, mint the self-host API key, print it + the URLs (used by `up`).
_access:
	@ADMIN_TOKEN=$$(grep -E '^ADMIN_TOKEN=' .env | cut -d= -f2-); \
	 APORT=$$(grep -E '^ADMIN_API_PORT=' .env | cut -d= -f2-); APORT=$${APORT:-18057}; \
	 GW=$$(grep -E '^API_GATEWAY_HOST_PORT=' .env | cut -d= -f2-); GW=$${GW:-18056}; \
	 UI=$$(grep -E '^TERMINAL_PORT=' .env | cut -d= -f2-); UI=$${UI:-13000}; \
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
