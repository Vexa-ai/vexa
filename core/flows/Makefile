# core/flows — the brick's verbs. `make help` lists them.
SHELL := /bin/bash
SRC   := src
PY    := python3
KEY   ?= changeme
API   ?= http://localhost:18200

.PHONY: help test storm gates schema run-worker run-mailbox run-api run stop status flows \
        submit check export logs

help: ## this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

test: ## full offline suite (fixtures · shapes · hostile · loopback · drift) — <1s, zero deps
	cd $(SRC)/.. && $(PY) -m pytest tests -q

storm: ## adversarial randomized storm (STORM_ROUNDS=n, STORM_SEED=n to replay)
	cd $(SRC)/.. && STORM_ROUNDS=$${STORM_ROUNDS:-500} $(PY) -m pytest tests/test_storm.py -q

gates: ## isolation (stdlib-pure engine · no-sleep law) + schema drift
	node scripts/check-isolation.js
	cd $(SRC)/.. && $(PY) -m pytest tests/test_schema_drift.py -q

schema: ## regenerate schema.sql from flows_schema models (the SSOT)
	$(PY) scripts/gen_schema.py

vocab-docs: ## regenerate docs/flows/vocabulary.mdx from step docstrings
	$(PY) scripts/gen_vocab_docs.py

run-worker: ## the production loop (durable Postgres; VEXA_FLOWS_* env)
	cd $(SRC) && VEXA_FLOWS_FIXTURE_TRANSCRIPT=$${VEXA_FLOWS_FIXTURE_TRANSCRIPT:-1} \
	  nohup $(PY) -m flows_worker >/tmp/flows-worker.log 2>&1 & echo "worker → /tmp/flows-worker.log"

run-mailbox: ## the mailbox integration (real inbox → facts; durable cursor)
	cd $(SRC) && nohup $(PY) -m flows_integrations.mailbox >/tmp/flows-mailbox.log 2>&1 & \
	  echo "mailbox → /tmp/flows-mailbox.log"

run-api: ## flows-api on :18200 (FastAPI; /docs)
	cd $(SRC) && nohup $(PY) -m flows_integrations.flows_api >/tmp/flows-api.log 2>&1 & \
	  echo "api → /tmp/flows-api.log · http://localhost:18200/docs"

run: run-worker run-mailbox run-api ## all three processes

stop: ## stop all three
	-pkill -f flows_worker; -pkill -f flows_integrations.mailbox; -pkill -f flows_integrations.flows_api
	@echo stopped

status: ## the operator projection (reactions, newest first)
	@curl -s -H "X-Flows-Admin-Key: $(KEY)" "$(API)/reactions" | $(PY) -m json.tool | head -60

flows: ## list every flow version + the step vocabulary
	@curl -s -H "X-Flows-Admin-Key: $(KEY)" "$(API)/flows" | $(PY) -m json.tool | head -80

check: ## validate a Python flow file: make check FILE=my_flow.py
	cd $(SRC) && $(PY) flows_authoring.py check $(FILE)

submit: ## submit a Python flow file: make submit FILE=my_flow.py
	cd $(SRC) && $(PY) flows_authoring.py submit $(FILE) --api $(API) --key $(KEY)

export: ## live registry → canonical Python (lossless): make export [NAME=flow]
	cd $(SRC) && $(PY) flows_authoring.py export $(NAME) --api $(API) --key $(KEY)

logs: ## tail all three process logs
	tail -n 20 /tmp/flows-worker.log /tmp/flows-mailbox.log /tmp/flows-api.log
