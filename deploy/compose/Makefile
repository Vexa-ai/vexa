# deploy/compose — the v0.12 control-plane stack (P4) + its readiness proof (P5, gate:compose).
#
# The stack-readiness proof (tests/stack_test.py) owns the full up→prove→down lifecycle in its
# session fixture, so `make stack-test` is the one entrypoint the gate calls. The bare up/down
# targets below are for hand-driving the stack during development.
PROJECT ?= vexa-v012
BROWSER_IMAGE ?= mock-bot:dev
COMPOSE  = docker compose -p $(PROJECT) -f docker-compose.yml

.PHONY: stack-test stack-test-bot stack-test-mock dashboard-harness up down build ps logs

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
## (build: docker build -f ../../meetings/services/bot/Dockerfile.mock -t mock-bot:dev ../../). Runs
## the always-on subset PLUS the MOCK_BOT scenarios (normal/reject/crash/timeout/emit/stop/max-bots/
## speak/canAccess) — no browser/STT. Pass COMPOSE_PROJECT to isolate on a shared host (bbb).
stack-test-mock:
	MOCK_BOT=1 BROWSER_IMAGE=$(BROWSER_IMAGE) ./bin/stack-test

## up / down / build — hand-drive the stack (dev only; the proof manages its own lifecycle).
up:
	$(COMPOSE) up -d --build
down:
	$(COMPOSE) down -v --remove-orphans
build:
	$(COMPOSE) build
ps:
	$(COMPOSE) ps
logs:
	$(COMPOSE) logs -f
