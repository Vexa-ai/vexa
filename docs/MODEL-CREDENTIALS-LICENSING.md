# Model credentials & Anthropic licensing — how Vexa deployments stay on the right side

Vexa agent workers run the official `claude` CLI (interactive turns via the harness,
one-shot beats via `claude -p` in `core/agent/llm/claude_cli.py`). The credential that
powers them is chosen per deployment and per user (Settings → Models, resolution
user > global > env — see `control_plane/dispatch.py:overlay_model_config`). Which
credential you pick determines **which Anthropic terms you operate under**. This doc is
the operating rule, with the primary sources.

## The primary sources

| Document | URL |
|---|---|
| Claude Code — Legal and compliance (the authentication rules quoted below) | <https://code.claude.com/docs/en/legal-and-compliance> |
| Consumer Terms of Service (Free / Pro / Max subscriptions) | <https://www.anthropic.com/legal/consumer-terms> |
| Commercial Terms of Service (API / Team / Enterprise) | <https://www.anthropic.com/legal/commercial-terms> |
| Anthropic Usage Policy (AUP) | <https://www.anthropic.com/legal/aup> |
| Use Claude Code with your Pro or Max plan (Help Center) | <https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan> |
| Use the Claude Agent SDK with your Claude plan (Agent SDK credit rules) | <https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan> |

The load-bearing sentences, from the Claude Code legal page (retrieved 2026-07-09):

> **OAuth authentication** is intended exclusively for purchasers of Claude Free, Pro,
> Max, Team, and Enterprise subscription plans and is designed to support ordinary use
> of Claude Code and other native Anthropic applications.

> **Developers** building products or services that interact with Claude's capabilities,
> including those using the Agent SDK, should use API key authentication through Claude
> Console or a supported cloud provider. **Anthropic does not permit third-party
> developers to offer Claude.ai login or to route requests through Free, Pro, or Max
> plan credentials on behalf of their users.**

> Advertised usage limits for Pro and Max plans assume ordinary, individual usage of
> Claude Code and the Agent SDK.

## What Vexa does, mapped to those rules

**Compliant by construction:**

- Workers invoke the **official `claude` binary** — never the raw HTTP API with an OAuth
  token. The subscription credential file (`~/.claude/.credentials.json`) is bind-mounted
  read-only into worker containers at the CLI's own standard path
  (`/root/.claude/.credentials.json`); only the official client consumes it. We do not
  extract the token into headers, third-party clients, or proxies.
- `claude -p` beats are plain subprocess calls of the compiled CLI. Per the Agent SDK
  help article, such programmatic use draws from the plan's **Agent SDK monthly credit**
  (per-user, non-pooled; overage at API rates if enabled) — it is metered, not masked.
  Nothing in the stack simulates a terminal/PTY to disguise programmatic calls as
  interactive use.
- Raw-API auth (`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`, `VEXA_LLM_*`) is only ever
  brokered in **custom** mode, where the credential is an API key the deployment or user
  supplied — Commercial Terms, no consumer restrictions.

**The boundary — one human, one subscription, one beneficiary:**

- **Self-host, single user** (your machine, your subscription, your turns): subscription
  mode as the deployment default is ordinary individual use. Fine.
- **Any multi-user or hosted deployment**: the operator's personal subscription must NOT
  serve other users' turns — that is "routing requests through Pro/Max plan credentials
  on behalf of their users", the explicitly disallowed case. The deployment default must
  be an **API key** (Settings → Models → Custom endpoint, or `ANTHROPIC_API_KEY` in the
  deployment env), or each user brings their own credential via user-level settings.
  Vexa as a hosted product must never default tenants onto anyone's consumer OAuth.

## Operator checklist

1. Exactly one human benefits from a mounted subscription credential. If
   `HOST_CLAUDE_CREDENTIALS` is set and more than one active user runs turns on the
   deployment, switch the deployment default to an API key. (Candidate guardrail: the
   config.v1 preflight / Settings → Models test button flagging
   subscription-credentials + multiple-active-users as a configuration smell.)
2. Never move the OAuth token out of the credentials file — no proxies, no header
   injection, no gateway re-serving. API-style env vars are for API keys only.
3. Expect programmatic (`claude -p`) usage to draw the Agent SDK credit, separately from
   interactive limits; exhaustion is a billing event, not a bug (the worker surfaces it
   as a model-inference failure).
4. When in doubt for a new topology, it's an API key — the Commercial Terms path has no
   authentication-shape restrictions.

*Maintainer note: terms evolve (the Agent SDK credit rules changed during 2026 and the
help article notes pending updates). Re-verify the links above before relying on this
doc for a new deployment class; update the retrieved-on date when you do.*
