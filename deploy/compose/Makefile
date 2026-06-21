# deploy/compose — the v0.12 control-plane stack (P4) + its readiness proof (P5, gate:compose).
#
# The stack-readiness proof (tests/stack_test.py) owns the full up→prove→down lifecycle in its
# session fixture, so `make stack-test` is the one entrypoint the gate calls. The bare up/down
# targets below are for hand-driving the stack during development.
PROJECT ?= vexa-v012
COMPOSE  = docker compose -p $(PROJECT) -f docker-compose.yml

.PHONY: stack-test stack-test-bot up down build ps logs

## stack-test — the always-on gate:compose proof (brings the stack up + down itself).
stack-test:
	./bin/stack-test

## stack-test-bot — + the real bot-spawn proof (COMPOSE_BOT=1; slow, needs vexaai/vexa-bot:dev).
stack-test-bot:
	COMPOSE_BOT=1 ./bin/stack-test

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
