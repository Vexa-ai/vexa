"""The worked model catalog the model-choice tests share: one provider of each adapter kind — a
self-hosted Qwen 3 on an OpenAI-compatible endpoint, OpenRouter (admins only), Anthropic on the
deployment's subscription, and the person's own endpoint — and the environment its secret_ref
resolves in. It is the contract's golden (`models.v1/golden/Catalog.self-hosted-and-openrouter.json`)
as a dict; `test_model_catalog` holds the two equal."""
from __future__ import annotations

ENV = {"OPENROUTER_API_KEY": "operator-openrouter-test-value",
       "ANTHROPIC_DIRECT_KEY": "operator-anthropic-test-value"}

#: The two worked examples the docs and the deploy values carry, plus one of each other kind.
EXAMPLE = {
    "providers": {
        "lab-vllm": {"adapter": "openai_compatible", "base_url": "http://10.0.0.5:8000/v1",
                     "auth": "none",
                     "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        "openrouter": {"adapter": "openrouter", "auth": "secret",
                       "secret_ref": "env:OPENROUTER_API_KEY"},
        "anthropic": {"adapter": "anthropic", "auth": "subscription"},
        "own": {"adapter": "custom"},
    },
    "models": [
        {"id": "qwen3-32b", "display_name": "Qwen 3 32B (self-hosted)", "provider": "lab-vllm",
         "model": "Qwen/Qwen3-32B",
         "capabilities": {"tool_calling": True, "streaming": True, "context_tokens": 32768},
         "default": True},
        {"id": "or-sonnet", "display_name": "Claude Sonnet via OpenRouter",
         "provider": "openrouter", "model": "anthropic/claude-sonnet-4.5", "access": "admins"},
        {"id": "claude", "display_name": "Claude (subscription)", "provider": "anthropic",
         "model": "claude-sonnet-4-5"},
        {"id": "mine", "display_name": "My endpoint", "provider": "own"},
    ],
}
