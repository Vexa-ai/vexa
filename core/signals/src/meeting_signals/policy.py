"""The company's switches and caps for signals.

agent-api reads `_global/POLICIES.md` and hands the four keys over with every brief
(``GET /internal/meetings/{row}/listen`` → ``policy``), so signals reads no policy file itself:

* ``transcript_signals`` — ``on`` or ``off`` (default ``off``);
* ``model_signals`` — the signal model (signals.v1 `ModelSignals`): a map, or its text written
  inline as JSON or as a flow map ``{adapter: jev, route: typesafe/jev-1.13, secret_ref:
  broker:signals/openrouter}``; or the models the company allows by tier, as JSON
  ``{"cheapest": {…}, "small": {…}}`` — the agent's brief picks a tier (``model_tier``), never a model;
* ``signals_wakes_per_meeting`` — wakes per source per HOUR of meeting before the model stops being called (default 12);
* ``signals_cost_per_meeting_usd`` — estimated model spend per source (default 0.50).

**Off unless both** ``transcript_signals: on`` and a valid ``model_signals``. Fail closed, always:
no brief answer, no ``policy`` in it, or an unreadable value is ``off``, named in a log line, and
signals calls no model. A cap above its ceiling is the ceiling.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("signals.policy")


DEFAULT_WAKES = 12                      # the POLICIES seed and agent-api say 12
DEFAULT_COST_USD = 0.50
CEILING_WAKES = 500
CEILING_COST_USD = 20.0
ADAPTERS = ("jev", "openai-compatible")
TIERS = ("cheapest", "small")
_SECRET_REF = re.compile(r"^(broker:signals/[a-z0-9][a-z0-9_.-]{0,62}|none)$")


@dataclass(frozen=True)
class Policy:
    enabled: bool
    model: Optional[dict]                 # the cheapest allowed model (the only one, when untiered)
    wakes_cap: int = DEFAULT_WAKES
    cost_cap_usd: float = DEFAULT_COST_USD
    models: tuple = ()                    # ((tier, model), …): the models the company allows, by tier

    def model_for(self, tier: str) -> Optional[dict]:
        """The model for the brief's tier — only one the policy names; else the cheapest."""
        allowed = dict(self.models)
        return allowed.get(tier) or allowed.get("cheapest") or self.model

    @property
    def active(self) -> bool:
        return self.enabled and self.model is not None


OFF = Policy(False, None)


def parse_model(value: str) -> dict:
    """``model_signals`` → a validated dict, or ValueError naming what is wrong (never the value)."""
    text = (value or "").strip()
    if not text:
        raise ValueError("model_signals is empty")
    try:
        data = json.loads(text)
    except ValueError:
        if not (text.startswith("{") and text.endswith("}")):
            raise ValueError("model_signals is not a map") from None
        data = {}
        for part in text[1:-1].split(","):
            k, sep, v = part.partition(":")
            if not sep:
                raise ValueError("model_signals has an entry without a value") from None
            data[k.strip().strip("\"'")] = v.strip().strip("\"'")
    if not isinstance(data, dict):
        raise ValueError("model_signals is not a map")
    unknown = set(data) - {"adapter", "route", "secret_ref", "base_url", "thinking"}
    if unknown:
        raise ValueError(f"model_signals has unknown keys: {sorted(unknown)}")
    if data.get("adapter") not in ADAPTERS:
        raise ValueError(f"model_signals.adapter must be one of {ADAPTERS}")
    route = str(data.get("route") or "").strip()
    if not route or len(route) > 300:
        raise ValueError("model_signals.route is missing")
    ref = str(data.get("secret_ref") or "none").strip()
    if not _SECRET_REF.match(ref):
        raise ValueError("model_signals.secret_ref must be broker:signals/<name> or none")
    base = str(data.get("base_url") or "").strip()
    if base and not re.match(r"^https?://[^\s]+$", base):
        raise ValueError("model_signals.base_url must be an http(s) URL")
    if base and data["adapter"] != "openai-compatible":
        raise ValueError("model_signals.base_url is for the openai-compatible adapter only")
    # THINKING (founder decision 14, 2026-10-17): a self-hosted reasoning model (Qwen) scores in
    # ~0.2 s with its thinking switched off. `off|on`, openai-compatible only; absent = the server's own.
    thinking = str(data.get("thinking") or "").strip().lower()
    if thinking and thinking not in ("off", "on"):
        raise ValueError("model_signals.thinking must be off or on")
    if thinking and data["adapter"] != "openai-compatible":
        raise ValueError("model_signals.thinking is for the openai-compatible adapter only")
    out = {"adapter": data["adapter"], "route": route, "secret_ref": ref}
    if base:
        out["base_url"] = base.rstrip("/")
    if thinking:
        out["thinking"] = thinking
    return out


def _number(attrs: dict, key: str, default, ceiling, cast, problems: list):
    raw = str(attrs.get(key, "") if attrs.get(key) is not None else "").strip().strip("\"'")
    if not raw:
        return default
    try:
        v = cast(raw)
    except ValueError:
        problems.append(f"{key}: not a number — using {default}")
        return default
    if v < 0:
        problems.append(f"{key}: negative — using {default}")
        return default
    if v > ceiling:
        problems.append(f"{key}: above the ceiling {ceiling} — using {ceiling}")
        return ceiling
    return v


def resolve(policy) -> tuple:
    """``(Policy, problems)`` from the ``policy`` map agent-api answered with (None = none)."""
    attrs = {str(k).lower(): v for k, v in policy.items()} if isinstance(policy, dict) else {}
    problems: list = []
    flag = attrs.get("transcript_signals", "")
    enabled = flag is True or str(flag).strip().strip("\"'").lower() in ("on", "true", "yes")
    model, models = None, ()
    raw_model = attrs.get("model_signals")
    if isinstance(raw_model, str) and raw_model.strip().startswith("{"):
        try:
            raw_model = json.loads(raw_model)
        except ValueError:
            pass
    try:
        if isinstance(raw_model, dict) and set(raw_model) and set(raw_model) <= set(TIERS):
            models = tuple((t, parse_model(json.dumps(raw_model[t]))) for t in TIERS if t in raw_model)
            model = dict(models).get("cheapest") or models[0][1]
        elif raw_model:
            model = parse_model(raw_model if isinstance(raw_model, str) else json.dumps(raw_model))
            models = (("cheapest", model),)
    except ValueError as exc:
        model, models = None, ()
        problems.append(f"model_signals: {exc} — signals stays off")
    if enabled and model is None and not problems:
        problems.append("transcript_signals is on but no model_signals is set — signals stays off")
    return Policy(
        enabled=enabled, model=model, models=models,
        wakes_cap=int(_number(attrs, "signals_wakes_per_meeting", DEFAULT_WAKES, CEILING_WAKES, int, problems)),
        cost_cap_usd=float(_number(attrs, "signals_cost_per_meeting_usd", DEFAULT_COST_USD, CEILING_COST_USD,
                                   float, problems)),
    ), problems
