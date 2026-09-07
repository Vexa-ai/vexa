"""The instance gate — is there a COMPANY behind this Vexa yet?

A fresh Vexa has flows, steps and a mailbox before it has any idea who it works for. The thin
company layer in `_global` — who we are, what we do, who is inside — is written and committed by
the instance administrator during setup, and until that commit lands every recipe in
`flows_defs/production.py` is a machine that will happily mail a stranger on behalf of nobody.
That is the expensive direction. The cheap direction is a touch that arrives a few minutes late.

So this module answers exactly one question — "may work run?" — and it answers it FAIL-CLOSED.
`admin-api` unreachable, a 500, a missing key, a body that is not the document we expect: all of
them read `missing`, because none of them is evidence that setup finished. The only thing that
opens the gate is admin-api saying, in so many words, `global_setup == "completed"`.

ONE EXCEPTION, and it is by construction rather than a probe (F-D15, ADR-0037 / PRD decision
40.7): the company layer is a feature OF the agent domain — its only writer is agent-api's
onboarding wizard (`POST /api/global/ready`) — so a deployment that does not run the agent
domain (`flows_steps.common.domain_present("agent")` is `False`) has no wizard that could ever
satisfy this gate. Without this exception a fresh no-agents instance admits every invite and
parks it FOREVER, indistinguishable from a broken product. When the agent domain IS deployed,
nothing below changes: fail-closed until the wizard commits.

    GET {VEXA_FLOWS_ADMIN_API_URL}/admin/instance     header X-Admin-API-Key
      -> {"admin_exists": bool, "global_setup": "completed" | "missing", "company": str | null}

The door is the one the flows tier already holds (`flows_steps.common.ADMIN_API` /
`require_admin_key()`) —
one deployment fact, one variable, never two spellings of one host.

TWO THINGS THIS MODULE IS CAREFUL ABOUT, because both were cheap to get wrong:

* **The cache.** `loop.tick` runs continuously — once per second on an idle worker, far more often
  under load — and it asks the gate before every reaction. An uncached read would put admin-api
  under a permanent poll from every worker replica, and a gate that DDoSes the service it reads is
  a gate that fails closed for the wrong reason. So the answer is held in-process for ~20s. Setup
  is a once-per-instance event measured in minutes; twenty seconds of staleness costs one delayed
  touch and buys three orders of magnitude fewer requests. `force=True` skips the cache for the
  callers who must not be stale (an operator asking "why is nothing moving?").

* **The log.** "The gate is up" is not one fact, it is two, and an operator staring at
  `flows-worker.log` needs to tell them apart: a gate up because admin-api is DOWN is an incident,
  a gate up because the admin has not finished typing is Tuesday. So every read carries a REASON,
  and the reason is printed on TRANSITION — when it changes — not on every call. A line per tick
  is a line nobody reads; a line per change is the timeline of the incident.

Test/dev seam: `VEXA_FLOWS_INSTANCE_GATE=completed|missing` short-circuits the HTTP read entirely.
It exists so the unit suite and a local rig can drive both sides of the gate with no admin-api and
no network. It is NOT a production switch: setting it in a deployment pins the gate to a constant
and defeats the whole point of asking. Any other value is ignored — a typo must not open the door.
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

from flows_steps import common

# The sentence, spelled ONE way, everywhere it is shown to a human. The gate refuses in several
# places (the loop, two operator verbs, both intake endpoints) and a person who meets it twice in
# two wordings has to work out whether they hit the same wall — so it lives here, once.
SETUP_SENTENCE = "This Vexa is being set up by its administrator."

TTL_S = 20.0

_CACHE: Optional[Tuple[str, float]] = None      # (state, monotonic expiry)
_LAST_LOGGED: Optional[str] = None              # the last state+reason we printed


def reset_cache() -> None:
    """Forget the cached answer and the last logged line. TEST/dev seam — the unit suite drives
    the gate from both sides in one process, and a 20s cache leaking across tests would make the
    second assertion read the first test's world."""
    global _CACHE, _LAST_LOGGED
    _CACHE = None
    _LAST_LOGGED = None


def _log_transition(state: str, why: str) -> None:
    line = f"{state} — {why}"
    global _LAST_LOGGED
    if line == _LAST_LOGGED:
        return                                   # same truth as last time: nothing happened
    _LAST_LOGGED = line
    print(f"[instance-gate] {line}", flush=True)


def _read() -> Tuple[str, str]:
    """One live read: (state, human reason). Never raises — every failure IS an answer here."""
    override = (os.environ.get("VEXA_FLOWS_INSTANCE_GATE") or "").strip().lower()
    if override in ("completed", "missing"):
        return override, f"VEXA_FLOWS_INSTANCE_GATE={override} (test/dev override, no http read)"

    # F-D15 (ADR-0037 / PRD decision 40.7 — a capability absent degrades, it does not park): the
    # ONLY writer of the company layer today is agent-api's onboarding wizard (POST
    # /api/global/ready -> PUT /internal/settings/global_setup). A deployment that does not run
    # the agent domain has no wizard, so without this check a fresh no-agents instance admits every
    # invite and parks it FOREVER — nothing in that profile can ever satisfy the gate. Presence is
    # the same signal every `needs=("agent",)` step already reads (`common.domain_present`), so
    # this is not a new concept: the company layer is a feature OF the agent domain, and when that
    # domain is not deployed there is no company layer to gate behind. When the agent domain IS
    # present, behaviour is byte-for-byte unchanged below — fail-closed until the wizard commits.
    if not common.domain_present("agent"):
        return "completed", "no agent domain: company layer gate open (F-D15 / ADR-0037)"

    url = f"{common.ADMIN_API.rstrip('/')}/admin/instance"
    try:
        # `require_admin_key` raises when the key is unset or a placeholder, and this `except`
        # turns that into "missing" — the fail-closed answer. A deployment with no admin identity
        # is exactly the state this gate exists to park the engine in.
        code, body = common.http("GET", url, {"X-Admin-API-Key": common.require_admin_key()},
                                 timeout=5)
    except Exception as e:  # noqa: BLE001 — including StepError, which common.http raises
        # An unreachable admin-api is NOT permission to proceed. This branch is the whole reason
        # the module exists: the failure mode it prevents is a flow mailing a stranger because the
        # service that knows who we are happened to be restarting.
        return "missing", f"admin-api unreachable — {type(e).__name__}: {e}"[:240]
    if code != 200:
        return "missing", f"admin-api answered {code} to GET /admin/instance"
    if not isinstance(body, dict):
        return "missing", (f"admin-api returned {type(body).__name__}, not the instance document "
                           f"— treating as unset")
    if str(body.get("global_setup") or "").strip().lower() != "completed":
        return "missing", (f"the company layer is not committed "
                           f"(global_setup={body.get('global_setup')!r}, "
                           f"admin_exists={body.get('admin_exists')!r})")
    return "completed", f"company layer ready (company={body.get('company')!r})"


def gate_state(*, force: bool = False) -> str:
    """``"completed"`` or ``"missing"`` — the ONE reader of the instance gate in this tier.

    Cached for ~``TTL_S`` seconds in-process; ``force=True`` bypasses the cache. Fail-closed on
    every error path, so this function has no exceptional exit: a caller never has to decide what
    an exception means, which is exactly where a fail-OPEN would have crept back in.
    """
    global _CACHE
    now = time.monotonic()
    if not force and _CACHE is not None and _CACHE[1] > now:
        return _CACHE[0]
    state, why = _read()
    _CACHE = (state, now + TTL_S)
    _log_transition(state, why)
    return state


def company_layer_ready() -> bool:
    """True when work may run. The shape `loop.tick(gate=...)` wants: zero-arg, boolean."""
    return gate_state() == "completed"
