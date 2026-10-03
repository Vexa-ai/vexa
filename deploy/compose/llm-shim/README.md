# llm-shim — the Anthropic front door

**One sentence:** the workspace agent speaks the Anthropic dialect; self-hosted model servers speak
only the OpenAI dialect; this translates between them so a deployment can run on its own model.

## When you need it

| Path | Dialect it speaks | Needs the shim? |
|---|---|---|
| Extracts, meeting cards | OpenAI (`/v1/chat/completions`) | **No** — native |
| Chat / workspace turns (Claude Code harness) | Anthropic (`/v1/messages`) | **Yes**, unless the endpoint already serves both |

Hosted gateways that serve **both** dialects can be used directly. For native Bedrock access,
use the Bedrock configuration below. Raw **vLLM / Ollama / llama.cpp / TGI** serve only OpenAI: use the shim.

## Run it

```bash
docker compose --profile llm-shim up -d llm-shim
```

Then set the deployment's model config to the shim:

```
base_url = http://llm-shim:<container port in the compose file>
api_key  = (none needed)
model    = $LLM_SHIM_MODEL
```

**The shim has no auth of its own, by design.** Enabling LiteLLM's key management pulls in a
required database — a stateful component for a stateless translator. Instead it binds to loopback
and lives on the compose network only. **Never expose this port publicly:** it forwards to your
model carrying your upstream key.

## Helm

Enable `llmShim.enabled=true` and set `llmShim.upstream` (including `/v1`) and
`llmShim.model`. The service is `llm-shim`; its port is `llmShim.port` in `values.yaml`.
Point the agent's model base URL at that service and select the same model id.
Use `llmShim.existingSecret` for a Secret containing `LLM_SHIM_UPSTREAM_KEY`, or
`llmShim.upstreamKey` for a plain value. `llmShim.config` replaces the full LiteLLM
config; `llmShim.extraEnv` supplies provider variables or Secret references.

The chart creates a Deployment, Service and ConfigMap only when enabled. It uses a
ClusterIP with no ingress and no auth. ClusterIP is cluster-internal; it does not enforce
namespace isolation. Restrict access with the cluster's existing network controls.

## Bedrock

`config.bedrock.example.yaml` uses the native Bedrock provider, with no third-party
fallback. Run the shim inside the VPC with private Bedrock endpoint DNS configured;
network policy and endpoint access are operator prerequisites, not created by this config.
The agent calls the shim using model `bedrock-model`; its code and credential allowlist
stay unchanged. Keep provider credentials on the shim.

For compose, set `AWS_REGION` and `LLM_SHIM_BEDROCK_MODEL`, then run:

```bash
LLM_SHIM_CONFIG=./llm-shim/config.bedrock.example.yaml docker compose --profile llm-shim up -d llm-shim
```

The AWS credential chain can use `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
`AWS_SESSION_TOKEN` for temporary credentials. The service passes these through.
For Helm, load the same example with
`--set-file llmShim.config=deploy/compose/llm-shim/config.bedrock.example.yaml` and set:

```yaml
llmShim:
  enabled: true
  extraEnv:
    - name: AWS_REGION
      value: eu-west-1
    - name: LLM_SHIM_BEDROCK_MODEL
      value: your-bedrock-model-or-inference-profile-id
```

Use the pod's IAM role through the AWS credential chain. The cluster operator must
configure that role for the pod's service account; this chart does not provision IAM.

## Configure

| Variable | Meaning |
|---|---|
| `LLM_SHIM_UPSTREAM` | the OpenAI-compatible endpoint, **including `/v1`** |
| `LLM_SHIM_MODEL` | the model id that endpoint serves |
| `LLM_SHIM_UPSTREAM_KEY` | upstream key; `none` for a keyless local server |

### Server-specific fields — read this before blaming the model

Some servers make a non-standard field **load-bearing**. The measured case: a self-hosted Qwen on
vLLM returns **0% valid JSON** in thinking mode (it spends the whole budget reasoning) and **100%**
with thinking off. Extracts are structured output, so the difference is *works* vs *does not*:

Uncomment the block in `config.yaml`:

```yaml
extra_body:
  chat_template_kwargs:
    enable_thinking: false
```

It is a file edit rather than an environment variable on purpose: this is a fact about your server,
and a variable that silently does nothing would be the worst possible failure here — the model
would simply return unusable output with everything reporting healthy.

There is **no product default** for this — it is a fact about a specific server, and guessing one
would be a vendor assumption baked into a model-agnostic product.

## Pins, and why

Use the pinned image in the compose file (or the chart’s `llmShim.image`) on **Docker Hub** — `ghcr.io/berriai` refuses
anonymous pulls, which would break a fresh deployment with an opaque `denied`. Inside the image the
FastAPI pin is already correct; when running LiteLLM from source instead, `fastapi<0.116` is required: newer FastAPI removed `get_flat_dependant`, which
LiteLLM imports — the proxy fails at startup. Both pins are ours to carry; that is the cost of this
component, and it is the reason the product does not translate dialects itself.

## Fallback — local development only

`config.local.example.yaml` is **our** dev setup: our own inference box as the primary, a hosted
gateway as a fallback so the dev stack keeps working when that box is unreachable. Copy it to
`config.local.yaml` (gitignored — it carries a key) and point the service at it:

```bash
LLM_SHIM_CONFIG=./llm-shim/config.local.yaml docker compose --profile llm-shim run -d --service-ports --use-aliases \
  -e OPENROUTER_API_KEY -e LLM_SHIM_FALLBACK_MODEL llm-shim
```

Export both local-only variables before running; the shipped service does not forward them.

**A customer deployment gets neither.** It points at its own endpoint, and a fallback is not a
default we ship: it would mean that the moment their box is unreachable, meeting content goes to a
third party — automatically, silently, at 3am. An outage that stops the product is recoverable; an
outage that quietly exports a board meeting is not. If a particular deployment wants one, that is
their decision to write down, not our default.

### Why a server-specific field cannot leak into a fallback

`extra_body` is set **per model entry**, so a thinking-mode flag rides only the entry that needs it.
A hosted gateway neither needs nor accepts it. This is the reason such fields stay on the entry
rather than global.
