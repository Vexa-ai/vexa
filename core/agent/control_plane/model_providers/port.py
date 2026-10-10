"""port.py — the provider port: a chosen catalog entry in, the route its turns run on out.

ONE JOB. ``ModelProviderPort.route`` turns one model of the operator's catalog into a
:class:`ModelRoute`: which harness drives it, which endpoint it is sent to, where the credential
comes from (and its value, when it is a secret this process holds), the model's id at the provider,
the extra request fields, and what the model can do. It decides nothing else and writes nothing:
``control_plane.dispatch`` turns the route into the worker's environment, in the one place that has
always decided a worker's route.

An adapter is one provider KIND — ``openai_compatible``, ``openrouter``, ``anthropic``, ``custom`` —
and is data plus a little code: the harnesses it can drive, the declaration fields it takes, and the
``route`` that maps a declaration to a route. Adding a provider kind is adding an adapter
(``adapters/``) and its enum entry in ``models.v1``; the dispatch never changes.

A refusal is a :class:`ModelChoiceFault` (P18): typed (``source`` + ``kind``), attributed (the model
and the provider), and carrying a sentence the person can act on. It is decided before any request
is made, so it never names an upstream's words.

ONE FAULT SHAPE FOR THE MODEL PROVIDER, TWO PRODUCERS. A provider call that fails is
``llm.faults.ProviderFault`` — the harness classifies it inside the worker. A pick that cannot run
fails here, in agent-api, before any call, and agent-api's image carries no ``llm/`` (the worker's
module, kept liftable). So this class produces the SAME record — ``source: model-provider`` and the
same keys, ``status`` always ``None`` — and its kinds are the ones only agent-api can decide.
``tests/test_model_provider_port.py`` holds the two key sets and the source equal, and the terminal
renders both through ``surfaces/faults.ts``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Protocol

from contracts import model_id_pattern
from shared.fault_wire import ModelProvider as _V

# THE VOCABULARY IS unit.v1's (`Fault`, `ModelProviderFaultKind`), generated into
# `shared/fault_wire.py` — the same source and kinds a failed provider call carries.
SOURCE = _V.SOURCE

#: The chat (or routine) names a model the catalog does not have.
UNKNOWN_MODEL = _V.UNKNOWN_MODEL
#: The model exists and this person may not use it (an admins-only entry).
NOT_PERMITTED = _V.NOT_PERMITTED
#: The entry needs something of the person's that they have not set (a ``custom`` entry with no
#: endpoint under Settings → Models), or the catalog offers this person nothing at all.
NOT_CONFIGURED = _V.NOT_CONFIGURED
#: The secret the provider's ``secret_ref`` names is not in agent-api's environment.
CREDENTIAL_MISSING = _V.CREDENTIAL_MISSING
#: The person's own endpoint cannot carry the turn (``model_endpoint.route_refusal``).
ENDPOINT_REFUSED = _V.ENDPOINT_REFUSED

KINDS = (UNKNOWN_MODEL, NOT_PERMITTED, NOT_CONFIGURED, CREDENTIAL_MISSING, ENDPOINT_REFUSED)

#: What agent-api answers its caller for each kind. Never 500: agent-api did not break.
_HTTP_STATUS = {UNKNOWN_MODEL: 422, NOT_PERMITTED: 403, NOT_CONFIGURED: 409,
                CREDENTIAL_MISSING: 503, ENDPOINT_REFUSED: 409}

# Where a route's credential comes from.
CRED_NONE = "none"                  # a keyless endpoint: every credential name is stamped empty
CRED_SECRET = "secret"              # the value of the provider's secret_ref
CRED_SUBSCRIPTION = "subscription"  # the deployment's own Claude subscription, brokered as today
CRED_SUBJECT = "subject"            # the person's own key, for the person's own endpoint only

HARNESSES = ("claude-code", "openai-agent", "codex")

_MODEL_ID = re.compile(model_id_pattern())


def is_model_id(value: str) -> bool:
    """Whether ``value`` is shaped like a catalog id (``models.v1#/$defs/ModelId``). Whether it
    names a model is the catalog's question, not this one."""
    return bool(_MODEL_ID.match(str(value or "")))


class ModelChoiceFault(Exception):
    """A model choice that cannot run, typed (P18). ``as_dict()`` is the shape it travels in."""

    source = SOURCE

    def __init__(self, kind: str, *, model: str, provider: str = "", detail: str,
                 remedy: str = "") -> None:
        self.kind = kind
        self.model = model
        self.provider = provider
        self.detail = detail
        self.remedy = remedy
        super().__init__(f"model {model or '?'} ({provider or '?'}): {kind} — {detail}")

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.kind, 409)

    def as_dict(self) -> dict:
        """The fault as it travels: an SSE ``error`` event, a JSON error body, a log line.

        The key set is ``llm.faults.ProviderFault``'s, so one client renderer serves both. ``status``
        is always ``None``: nothing was asked of the provider."""
        return {"source": SOURCE, "kind": self.kind, "provider": self.provider,
                "model": self.model, "status": None, "detail": self.detail,
                "remedy": self.remedy}

    def sentence(self) -> str:
        """One sentence, for a client that renders only ``detail`` text."""
        return " ".join(x for x in (self.detail, self.remedy) if x)


@dataclass(frozen=True)
class Capabilities:
    """What a model can do on its route. ``streaming`` and ``context_tokens`` reach the openai-agent
    harness; ``tool_calling`` is what makes it able to drive an agent turn at all."""

    tool_calling: bool = True
    streaming: bool = True
    context_tokens: Optional[int] = None

    def as_dict(self) -> dict:
        return {"tool_calling": self.tool_calling, "streaming": self.streaming,
                "context_tokens": self.context_tokens}


@dataclass(frozen=True)
class ModelRoute:
    """Where one model's turns go, and with what — harness-neutral, decided by one adapter."""

    model_id: str            # the catalog id the person chose (attribution, never sent anywhere)
    provider: str            # the catalog's provider key (attribution)
    adapter: str             # the adapter kind that resolved it
    harness: str             # the agent harness that drives it
    base_url: str            # the endpoint; "" = the harness's own default (api.anthropic.com)
    credential_source: str   # CRED_NONE | CRED_SECRET | CRED_SUBSCRIPTION | CRED_SUBJECT
    provider_model: str      # the model's id at the provider; "" = the deployment's model
    extra_body: str          # a JSON object's text, merged into every openai-agent request; "" = none
    capabilities: Capabilities = field(default_factory=Capabilities)
    #: How the endpoint expects the key: ``bearer`` (``Authorization: Bearer``, every OpenAI-dialect
    #: endpoint and OpenRouter) or ``x-api-key`` (Anthropic's own API key header).
    auth_header: str = "bearer"
    #: The credential's value. Never in a repr, a log line, or anything the browser receives.
    credential: str = field(default="", repr=False)


@dataclass(frozen=True)
class RouteContext:
    """What an adapter may consult beyond its own declaration — injected, so a test passes plain
    values and an adapter never reads the process environment itself."""

    #: The person's effective Settings → Models config (user over platform), ``{}`` when none.
    subject_config: Mapping = field(default_factory=dict)
    #: A ``secret_ref`` → its value, ``""`` when the reference resolves to nothing.
    secret: Callable[[str], str] = lambda _ref: ""
    #: The rule on a person's own endpoint — ``(base_url, api_key, harness)`` → ``None`` when it may
    #: carry the turn, else why not (``model_endpoint.route_refusal``: the operator gate, and the
    #: person's own key on a harness that would otherwise sign in from its config directory).
    endpoint_refusal: Callable[[str, str, str], Optional[str]] = lambda _url, _key, _harness: None
    #: ``VEXA_MODEL_ALLOWLIST`` as a predicate — still the gate on a person's own free-form model
    #: name, and on nothing the operator declared.
    model_allowed: Callable[[str], bool] = lambda _model: True
    #: The deployment's own harness and model, for a route that keeps them (a ``custom`` entry whose
    #: owner named neither).
    deployment_runner: str = "claude-code"
    deployment_model: str = ""


class ModelProviderPort(Protocol):
    """One provider kind. Implementations live in ``adapters/``, one module each."""

    #: The ``adapter`` value a provider declaration names.
    kind: str
    #: The declaration fields a provider of this kind may carry (beyond ``adapter``).
    fields: tuple[str, ...]

    def check(self, provider: Mapping, models: list[Mapping]) -> list[str]:
        """What is wrong with this provider's declaration and the models on it, one problem per
        line, naming the field. ``[]`` = sound. Called once, at boot."""
        ...

    def harness(self, provider: Mapping, ctx: Optional[RouteContext] = None) -> str:
        """The harness that drives this provider's models."""
        ...

    def available(self, ctx: RouteContext) -> bool:
        """Whether a model on this provider can run for the person ``ctx`` describes at all — the
        person-specific half of visibility (``custom`` needs their own endpoint)."""
        ...

    def route(self, model: Mapping, provider_key: str, provider: Mapping,
              ctx: RouteContext) -> ModelRoute:
        """The route ``model`` runs on. Raises :class:`ModelChoiceFault` when it cannot run."""
        ...
