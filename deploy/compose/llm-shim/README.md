# llm-shim — the Anthropic front door

**One sentence:** the workspace agent speaks the Anthropic dialect; self-hosted model servers speak
only the OpenAI dialect; this translates between them so a deployment can run on its own model.

## When you need it

| Path | Dialect it speaks | Needs the shim? |
|---|---|---|
| Extracts, meeting cards | OpenAI (`/v1/chat/completions`) | **No** — native |
| Chat / workspace turns (Claude Code harness) | Anthropic (`/v1/messages`) | **Yes**, unless the endpoint already serves both |

Hosted gateways (OpenRouter, Bedrock-style proxies) serve **both** dialects — point straight at them
and skip this. Raw **vLLM / Ollama / llama.cpp / TGI** serve only OpenAI: use the shim.

## Run it

```bash
docker compose --profile llm-shim up -d llm-shim
```

Then set the deployment's model config to the shim:

```
base_url = http://llm-shim:4000     # from inside compose
api_key  = (none needed)
model    = $LLM_SHIM_MODEL
```

**The shim has no auth of its own, by design.** Enabling LiteLLM's key management pulls in a
required database — a stateful component for a stateless translator. Instead it binds to loopback
and lives on the compose network only. **Never expose this port publicly:** it forwards to your
model carrying your upstream key.

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

The image is pinned to `litellm/litellm:main-v1.97.0` on **Docker Hub** — `ghcr.io/berriai` refuses
anonymous pulls, which would break a fresh deployment with an opaque `denied`. Inside the image the
FastAPI pin is already correct; when running LiteLLM from source instead, `fastapi<0.116` is required: newer FastAPI removed `get_flat_dependant`, which
LiteLLM imports — the proxy fails at startup. Both pins are ours to carry; that is the cost of this
component, and it is the reason the product does not translate dialects itself.
