# deploy/dogfood — the long-lived MCP validation + dogfooding stack.
#
# The container targets (up/down/pull/logs/key) are meant to run ON THE STACK HOST (bbb), never on
# a laptop. The probe targets (validate/validate-local/connect) are pure HTTP and run anywhere.
#
# This drives the STOCK deploy/compose stack with a dogfood .env — there is no overlay and no fork.
# A dogfooding stack that differs from what users run stops being evidence about what users run.

DOGFOOD_ENV ?= .env.dogfood
COMPOSE_FILE ?= ../compose/docker-compose.yml
PROJECT ?= vexa-dogfood
COMPOSE = docker compose -p $(PROJECT) --env-file $(DOGFOOD_ENV) -f $(COMPOSE_FILE)

# Public front door (nginx → gateway). Override for a local/tunnelled probe.
MCP_URL ?= https://mcp.dev.vexa.ai/mcp
LOCAL_MCP_URL ?= http://127.0.0.1:18456/mcp
EMAIL ?= dmitry@vexa.ai
SCOPES ?= bot,tx

.PHONY: help up down pull ps logs key validate validate-local connect nginx-check env-check

help:
	@echo "  on the stack host (bbb):"
	@echo "    make up             pull published images + bring the dogfood stack up"
	@echo "    make down           stop it (volumes survive)"
	@echo "    make ps / logs      inspect it"
	@echo "    make key            mint + print an API key for EMAIL=$(EMAIL)"
	@echo "    make nginx-check    verify the two vhosts are installed and nginx is happy"
	@echo "  from anywhere:"
	@echo "    make validate       drive $(MCP_URL) as a real MCP client"
	@echo "    make validate-local same, against $(LOCAL_MCP_URL) (host-local, bypasses nginx)"
	@echo "    make connect        print the exact 'claude mcp add' line for this endpoint"

env-check:
	@[ -f $(DOGFOOD_ENV) ] || { \
	  echo "✗ no $(DOGFOOD_ENV) — cp env.dogfood.example $(DOGFOOD_ENV) and fill the CHANGE-ME values"; \
	  exit 1; }
	@if grep -q 'CHANGE-ME' $(DOGFOOD_ENV); then \
	  echo "✗ $(DOGFOOD_ENV) still has CHANGE-ME placeholders:"; \
	  grep -n 'CHANGE-ME' $(DOGFOOD_ENV) | sed 's/^/    /'; \
	  echo "  this stack is internet-reachable through nginx — the compose dev defaults are not acceptable here."; \
	  exit 1; fi

## up — pull the published, release-validated images and start. Never builds from source: the
## point of dogfooding is to live on the bytes a user would get.
up: env-check
	$(COMPOSE) pull
	$(COMPOSE) up -d --no-build
	@echo "→ stack up. gateway=127.0.0.1:18456  mcp=127.0.0.1:18410  terminal=127.0.0.1:15400"

down:
	$(COMPOSE) down

pull: env-check
	$(COMPOSE) pull

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100 $(SVC)

## key — mint an API key against THIS stack's admin-api, reusing deploy/compose/bin/provision-token
## (same job, already idempotent on the user). Prints ONLY the token, so it pipes.
## Note: the USER is resolve-or-create, but each run mints a FRESH token — run it once and keep the
## value; don't call it on a loop or you accumulate live tokens.
key: env-check
	@ADMIN_TOKEN="$$(grep -E '^ADMIN_TOKEN=' $(DOGFOOD_ENV) | head -1 | cut -d= -f2-)" \
	 ADMIN_API_URL="http://127.0.0.1:$$(grep -E '^ADMIN_API_PORT=' $(DOGFOOD_ENV) | head -1 | cut -d= -f2-)" \
	 EMAIL="$(EMAIL)" SCOPES="$(SCOPES)" ../compose/bin/provision-token

validate:
	@./bin/mcp-validate --url "$(MCP_URL)" $(if $(VEXA_API_KEY),--key "$(VEXA_API_KEY)",)

validate-local:
	@./bin/mcp-validate --url "$(LOCAL_MCP_URL)" $(if $(VEXA_API_KEY),--key "$(VEXA_API_KEY)",)

## connect — the one line a human runs to attach Claude Code to this endpoint. Today it carries a
## key in a header; when the OAuth work lands the --header disappears and this becomes a login.
connect:
	@echo 'claude mcp add --transport http vexa-dogfood $(MCP_URL) \'
	@echo '  --header "Authorization: Bearer $$VEXA_API_KEY"'
	@echo
	@echo 'Then: /mcp inside Claude Code to confirm it connected.'

nginx-check:
	@for h in mcp.dev.vexa.ai dogfood.dev.vexa.ai; do \
	  printf '  %-22s ' "$$h"; \
	  curl -sS -o /dev/null -w 'HTTP %{http_code} (TLS %{ssl_verify_result})\n' "https://$$h/health" || echo unreachable; \
	done
	@nginx -t 2>&1 | sed 's/^/  /' || true
