"""Typed join-failure evidence — the WHY behind a `failed` pre-active meeting (#1059, #1058).

Two production defects motivate this module, and they are the same defect seen from two sides:

  * **#1059 — the record is empty.** Every one of 83 hosted `join_failure` meetings carried
    ``data.reason = None``. Not because nobody knew: the bot stamps a human ``reason`` on every
    terminal it emits (``orchestrator.ts``), the reconcile sweep stamps the probe's own answer
    (``reconcile.py``), and the FSM stores it on ``MeetingRecord.reason``. It simply never reached
    the JSONB — ``MeetingRecord.data`` projected the reason ONLY into ``last_error.reason``, a key
    the list view drops (``projection.LIST_OMIT_KEYS``) and no aggregate query looks at. Diagnosis
    stopped at the shape of the failure.
  * **#1058 — the label conflates.** A ~20s Teams death (the platform refused us before a lobby
    ever rendered) and a ~13min Google Meet death (a bot that sat in the waiting room until its
    budget expired) both land as ``completion_reason = join_failure``. Same label, different owner,
    different fix, and any SLO built on it measures two unrelated things.

The fix for both is TWO axes this module owns, carried alongside — never instead of — the sealed
lifecycle.v1 ``CompletionReason`` (which stays untouched: it is the retry classifier's input, see
``retry.py``):

  * :class:`JoinFailureReason` — WHAT happened, derived from the signals we actually hold. This is
    the diagnostic axis #1058 calls missing.
  * :class:`JoinFailureAttribution` — WHO it belongs to: ``system_fault`` / ``user_action`` /
    ``host_action`` / ``exogenous_platform`` / ``unknown``. This is the axis the reliability gate
    reads, and the reason the raw failed-status rate is not a reliability metric: it counts a user
    who stopped their own bot and a host who never clicked admit as if the software had failed.
    With attribution persisted, the gate metric becomes a query::

        system_failure_rate = failures(attribution = system_fault) / fair-chance meetings

    per release and per platform. The two axes are genuinely independent — the SAME typed
    ``awaiting_admission_timeout`` is a host's choice normally, and ours the moment the evidence shows our
    admission flow is what broke.

**ATTRIBUTE, never manufacture.** Every value below is reachable from evidence the system already
holds: which stage the bot died in, whether it ever reported reaching the lobby, how long it spent
there against the budget the control plane itself issued, and the platform's own message. When the
signals do not support a verdict the answer is :attr:`JoinFailureReason.UNKNOWN` with the raw detail
preserved — an honest "we don't know" beats a plausible label nobody can trust.

**FAIL-OPEN.** Nothing here may change join behaviour. Every public entry point is total: it
returns a best-effort answer or ``None`` and never raises, so a malformed payload, a missing timing
or an unknown enum degrades the evidence and nothing else. Callers still wrap it (belt and braces) —
capturing evidence must never be able to fail a meeting that would otherwise have succeeded.

The bot has a mirror of this taxonomy (``services/bot/src/join-evidence.ts``) so it can classify at
the source, where the platform signal and the stage timings actually live; this module accepts that
verdict when it is present and DERIVES one when it is not (a reconcile-driven terminal, a
runtime-confirmed destroy, or a bot too old to send one). Either way the row gets evidence.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping, Optional

__all__ = [
    "JoinFailureReason",
    "JoinFailureAttribution",
    "DETAIL_MAX_CHARS",
    "LOBBY_EXPIRY_FRACTION",
    "classify_join_failure",
    "attribute_join_failure",
    "build_join_evidence",
    "evidence_from_event",
    "normalize_evidence",
]


class JoinFailureReason(str, Enum):
    """WHY a bot never reached the meeting — the diagnostic axis (#1058).

    Deliberately small and extensible: each value names a distinct OWNER and a distinct FIX, which
    is the whole point of splitting them. Add a value only when it would be acted on differently.

    **ONE WORD PER FACT.** Where this axis names a fact the sealed ``lifecycle.v1``/``api.v1`` enums
    already name, it reuses THEIR word — ``awaiting_admission_rejected``, ``awaiting_admission_timeout``,
    ``auth_session_missing``. A second dictionary for the same fact costs every reader a translation
    and every query a CASE arm, and the axes are layered rather than competing: this one adds the
    values the sealed enum does not have (the three-way split of ``join_failure``) and says nothing
    new about the ones it does. Where the fact is NOT the same, the word must not be either — which
    is why the user-stop value is ``stopped_while_joining`` and not ``stopped_before_admission``: the
    latter is a declared value of the sealed ``api.v1 MeetingCompletionReason``, on a different
    field, with a near-but-not-identical meaning. Two vocabularies may compose; they may not collide.

    * :attr:`AWAITING_ADMISSION_REJECTED` — the platform/host actively DENIED us. A moderator clicked
      deny, or the client refused the automated join outright (the sealed
      ``awaiting_admission_rejected`` path, a Zoom RTMS block, a bot-detection block). Deterministic
      and fast; a re-spawn hits the same wall. Owner: the account/meeting policy, not us.
    * :attr:`AWAITING_ADMISSION_TIMEOUT` — nobody ever answered. The bot reached the waiting room and
      sat there until the lobby budget the control plane issued
      (``bot_spawn.service.LOBBY_BUDGET_MS``) ran out. This is the ~13min Google Meet population of
      #1058: a REAPING, not a refusal. Owner: the meeting host (or our budget), not the join code.
    * :attr:`AUTH_SESSION_MISSING` — authenticated mode found a signed-out browser profile. The
      profile is dead; no re-spawn can fix it. Owner: session provisioning.
    * :attr:`NEVER_REACHED_LOBBY` — the meeting page loaded but no admission signal ever appeared:
      no lobby, no leave button, nothing the join layer recognises. This is the selector-rot /
      platform-UI-change class (#806) and the shape a fast Teams refusal takes when it renders no
      lobby at all. Owner: the join layer.
    * :attr:`NAVIGATION_FAILURE` — the bot never got as far as the meeting page: a navigation error,
      a redirect off the meeting origin (the Teams sign-in redirect), a closed target, an
      unsupported platform. Owner: infra / URL construction.
    * :attr:`STOPPED_WHILE_JOINING` — the USER ended the run while the bot was still knocking (the
      orchestrator's withdraw branch; the sealed ``lifecycle.v1`` reason is ``stopped``, and this
      value is deliberately NOT the sealed ``api.v1`` ``stopped_before_admission``). It lands with status
      ``failed`` because the bot never became active, and it is emphatically NOT a failure of ours —
      counting it as one is exactly what makes a raw failed-status rate meaningless.
    * :attr:`UNKNOWN` — the signals do not support any of the above. ``detail`` carries whatever the
      producer did observe. Never a synonym for "probably X".
    """

    AWAITING_ADMISSION_REJECTED = "awaiting_admission_rejected"
    AWAITING_ADMISSION_TIMEOUT = "awaiting_admission_timeout"
    AUTH_SESSION_MISSING = "auth_session_missing"
    NEVER_REACHED_LOBBY = "never_reached_lobby"
    NAVIGATION_FAILURE = "navigation_failure"
    STOPPED_WHILE_JOINING = "stopped_while_joining"
    UNKNOWN = "unknown"


class JoinFailureAttribution(str, Enum):
    """WHO the failure belongs to — the second axis, and the one the reliability gate reads.

    The typed reason says WHAT happened; this says whether it counts AGAINST US. They are genuinely
    independent: `awaiting_admission_timeout` is normally a host who never clicked admit (a funnel outcome),
    but the SAME typed reason is ours the moment the evidence shows our admission flow was the thing
    that broke. Collapsing the two axes is what made the raw failed-status rate uninterpretable —
    it counts user stops and host non-admissions as if the software had failed.

    The gate metric this exists to make computable::

        system_failure_rate = failures(attribution = system_fault) / fair-chance meetings

    per release and per platform, where a *fair-chance* meeting is one the system was actually given
    the opportunity to serve (excludes `user_action`: a run the user ended before admission was
    never ours to win).

    * :attr:`SYSTEM_FAULT` — WE failed to do our job: the join flow broke, navigation never landed,
      the authenticated session was lost, the bot crashed, the admission flow itself wedged. The
      numerator of the gate metric, and the only bucket a fix of ours can move.
    * :attr:`USER_ACTION` — the user ended the run themselves (a stop while the bot was still
      knocking). Not a failure in any sense that matters; excluded from the denominator entirely.
    * :attr:`HOST_ACTION` — the meeting's host/moderator decided: denied the bot, or never answered
      the knock. A funnel outcome, not a defect. Moves with customer behaviour, not with our code.
    * :attr:`EXOGENOUS_PLATFORM` — the platform itself refused or changed under us in a way we can
      POSITIVELY identify (an automated-join policy block, an identified client-side change). Real,
      actionable, and ours to absorb — but not a regression of our own making, which is exactly the
      endogenous/exogenous split a release gate must not blur.
    * :attr:`UNKNOWN` — the evidence does not support an owner. Counted separately and never quietly
      folded into `system_fault` (which would inflate the gate metric with our own ignorance) nor
      into `host_action` (which would hide real defects).
    """

    SYSTEM_FAULT = "system_fault"
    USER_ACTION = "user_action"
    HOST_ACTION = "host_action"
    EXOGENOUS_PLATFORM = "exogenous_platform"
    UNKNOWN = "unknown"


#: Cap the free-text platform signal so a runaway stack trace can never bloat ``meeting.data``
#: (same discipline as the ``bot_logs`` ring-buffer budget in ``machine.py``). Longer details are
#: TRUNCATED with an explicit ellipsis, never dropped — a clipped signal still names the cause.
DETAIL_MAX_CHARS = 500

#: How much of the issued lobby budget must have been burned for a lobby exit to read as EXPIRY
#: rather than a refusal. The budget is a deadline WE wrote, and the bot's own poll loop plus its
#: terminal callback land a little under it, so an exact-equality test would misclass every real
#: timeout. 0.9 of the budget is comfortably past any refusal (#1058's Teams cohort died at ~3% of
#: it) and comfortably inside any real expiry.
LOBBY_EXPIRY_FRACTION = 0.9

#: Sealed lifecycle.v1 ``CompletionReason`` values that ALREADY name the join failure precisely —
#: the typed reason is a direct read, no inference needed.
_REASON_BY_COMPLETION_REASON: Dict[str, JoinFailureReason] = {
    "auth_session_missing": JoinFailureReason.AUTH_SESSION_MISSING,
    "awaiting_admission_rejected": JoinFailureReason.AWAITING_ADMISSION_REJECTED,
    "awaiting_admission_timeout": JoinFailureReason.AWAITING_ADMISSION_TIMEOUT,
    # A pre-active `stopped` is the USER ending the run while the bot was still knocking (the
    # orchestrator's withdraw branch, and `reconcile._pre_active_completion_reason` under
    # `stop_requested`). It shares the `failed` status with real defects and must never share
    # their attribution.
    "stopped": JoinFailureReason.STOPPED_WHILE_JOINING,
}

#: Default owner per typed reason. Overridden only by POSITIVE evidence in the producer's own
#: message (see :data:`_PLATFORM_POLICY_MARKERS` / :data:`_ADMISSION_FLOW_FAULT_MARKERS`) — an
#: attribution is a claim about ownership, so it moves on evidence, never on a hunch.
_ATTRIBUTION_BY_REASON: Dict[JoinFailureReason, JoinFailureAttribution] = {
    # Someone/something on the far side said no. Default to the HOST (a moderator denying is the
    # overwhelmingly common case); a platform-policy refusal is detected below and reattributed.
    JoinFailureReason.AWAITING_ADMISSION_REJECTED: JoinFailureAttribution.HOST_ACTION,
    # Nobody answered the knock. The host's call — unless the evidence says our admission flow is
    # what broke, which is detected below.
    JoinFailureReason.AWAITING_ADMISSION_TIMEOUT: JoinFailureAttribution.HOST_ACTION,
    # Ours end to end: session provisioning, the join layer, the browser.
    JoinFailureReason.AUTH_SESSION_MISSING: JoinFailureAttribution.SYSTEM_FAULT,
    JoinFailureReason.NEVER_REACHED_LOBBY: JoinFailureAttribution.SYSTEM_FAULT,
    JoinFailureReason.NAVIGATION_FAILURE: JoinFailureAttribution.SYSTEM_FAULT,
    # The user ended it. Not a failure; excluded from the gate metric's denominator.
    JoinFailureReason.STOPPED_WHILE_JOINING: JoinFailureAttribution.USER_ACTION,
    JoinFailureReason.UNKNOWN: JoinFailureAttribution.UNKNOWN,
}

#: Producer-message markers that positively identify a PLATFORM-POLICY refusal rather than a human
#: decision: the client blocking automated joins outright, or bot-detection interposing. These are
#: exogenous — no fix of ours makes the platform say yes — so they must not be counted as either a
#: host's choice or our own defect.
_PLATFORM_POLICY_MARKERS = (
    "zoom_requires_rtms",
    "blocks automated browser joins",
    "recaptcha",
    "captcha",
    "bot detection",
    "bot-detection",
    "unusual traffic",
)

#: Producer-message markers that positively identify OUR admission flow as the thing that broke —
#: the join layer could not read the platform's state at all (selector rot, an indicator set that no
#: longer matches). A wait that ended this way is not a host declining to admit; it is us failing to
#: notice. This is the "UNLESS evidence shows the admission flow itself broke" carve-out.
_ADMISSION_FLOW_FAULT_MARKERS = (
    "no meeting indicators found",
    "could not determine",
    "selector",
    "waiting for selector",
    "no admission signal",
)

#: Substrings in the producer's own message that positively identify a NAVIGATION failure (the bot
#: never reached the meeting page). Matched case-insensitively against ``detail``. Kept narrow and
#: mechanical on purpose: these are transport/browser-level strings, not interpretations of meeting
#: state, so matching one is reading evidence rather than guessing at it.
_NAVIGATION_MARKERS = (
    "net::err",
    "err_name_not_resolved",
    "err_connection",
    "target closed",
    "target page, context or browser has been closed",
    "page closed",
    "navigating to",
    "navigation failed",
    "teams_auth_redirect",
    "teams_off_meeting_origin",
    "cannot infer platform",
    "unsupported platform",
    # Our own control plane being unreachable at boot: the bot deliberately never navigated to the
    # meeting. It never got as far as the page, which is what this reason means.
    "control_plane_unreachable",
)

#: Meeting statuses that mean the bot had NOT yet reported reaching the waiting room. Mirrors the
#: pre-active set in ``reconcile.py``; duplicated as strings here so this module stays importable
#: with no lifecycle dependencies (it is consumed by the FSM itself).
_PRE_LOBBY_STAGES = frozenset({"requested", "joining"})


def _clean_detail(detail: Any) -> Optional[str]:
    """The producer's own message, trimmed + capped. Non-strings and blanks project to ``None``."""
    if not isinstance(detail, str):
        return None
    text = detail.strip()
    if not text:
        return None
    if len(text) <= DETAIL_MAX_CHARS:
        return text
    return text[: DETAIL_MAX_CHARS - 1] + "…"


def _positive_int(value: Any) -> Optional[int]:
    """A non-negative millisecond count, or ``None``. Booleans are NOT ints here (``True`` is not
    a duration) and anything unparseable degrades to ``None`` rather than raising."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return ms if ms >= 0 else None


def _looks_like_navigation_failure(detail: Optional[str]) -> bool:
    if not detail:
        return False
    lowered = detail.lower()
    return any(marker in lowered for marker in _NAVIGATION_MARKERS)


def classify_join_failure(
    *,
    completion_reason: Optional[str] = None,
    stage: Optional[str] = None,
    reached_lobby: Optional[bool] = None,
    time_in_lobby_ms: Optional[int] = None,
    lobby_budget_ms: Optional[int] = None,
    detail: Optional[str] = None,
) -> JoinFailureReason:
    """Derive the typed join-failure reason from the signals a producer actually holds.

    Total: every input is optional and every unusable value degrades to ``UNKNOWN`` — never an
    exception. The order below is the order of EVIDENCE STRENGTH, strongest first:

    1. A sealed ``completion_reason`` that already names the cause is read directly.
    2. A transport-level marker in the producer's own message names a navigation failure — checked
       before the stage buckets because a browser that never loaded the page can die at any stage.
    3. The bot REACHED the lobby (reported ``awaiting_admission``): the only two ways out without
       admission are a denial (case 1, already handled) or the budget expiring. A lobby stay that
       burned :data:`LOBBY_EXPIRY_FRACTION` of the issued budget is an expiry; a lobby exit well
       inside the budget with no denial reason is the platform refusing us — the #1058 split, made
       from the timing signature.
    4. The bot never reached the lobby but did report ``joining``: it was on the page and no
       admission signal ever appeared (#806's selector-rot class).
    5. It never reported at all (``requested``): we genuinely do not know — ``UNKNOWN``, with the
       detail carrying whatever the reaper's probe saw.
    """
    try:
        named = _REASON_BY_COMPLETION_REASON.get(completion_reason or "")
        if named is not None:
            return named

        detail_text = _clean_detail(detail)
        if _looks_like_navigation_failure(detail_text):
            return JoinFailureReason.NAVIGATION_FAILURE

        in_lobby = bool(reached_lobby) or stage == "awaiting_admission"
        if in_lobby:
            lobby_ms = _positive_int(time_in_lobby_ms)
            budget_ms = _positive_int(lobby_budget_ms)
            if lobby_ms is not None and budget_ms:
                if lobby_ms >= budget_ms * LOBBY_EXPIRY_FRACTION:
                    return JoinFailureReason.AWAITING_ADMISSION_TIMEOUT
                # Out of the lobby well inside the budget, and no denial reason attached: the
                # platform ended the wait, we did not. That is a rejection in everything but name.
                return JoinFailureReason.AWAITING_ADMISSION_REJECTED
            # No usable timing (the reconcile sweep holds none): the stage is still evidence —
            # a bot reaped IN the waiting room exhausted the wait, because a denial would have
            # arrived as its own sealed reason.
            return JoinFailureReason.AWAITING_ADMISSION_TIMEOUT

        if stage == "joining" or reached_lobby is False:
            return JoinFailureReason.NEVER_REACHED_LOBBY

        if stage in _PRE_LOBBY_STAGES:
            return JoinFailureReason.UNKNOWN

        return JoinFailureReason.UNKNOWN
    except Exception:  # noqa: BLE001 — classification must never raise into the join path
        return JoinFailureReason.UNKNOWN


def attribute_join_failure(
    reason: JoinFailureReason, *, detail: Optional[str] = None
) -> JoinFailureAttribution:
    """WHO the failure belongs to — the gate-metric axis (see :class:`JoinFailureAttribution`).

    Starts from the typed reason's default owner and moves it ONLY on positive evidence in the
    producer's own message:

    * a platform-policy refusal (automated joins blocked, bot-detection) reattributes a rejection
      from the host to :attr:`~JoinFailureAttribution.EXOGENOUS_PLATFORM` — no host decided that,
      and no fix of ours reverses it;
    * an admission wait that ended because our join layer could not read the platform's state at
      all reattributes from the host to :attr:`~JoinFailureAttribution.SYSTEM_FAULT` — the host
      never got the chance to decline.

    Total: an unknown reason attributes to ``UNKNOWN`` and nothing here raises.
    """
    try:
        base = _ATTRIBUTION_BY_REASON.get(reason, JoinFailureAttribution.UNKNOWN)
        text = (_clean_detail(detail) or "").lower()
        if not text:
            return base
        if reason is JoinFailureReason.AWAITING_ADMISSION_REJECTED and any(
            marker in text for marker in _PLATFORM_POLICY_MARKERS
        ):
            return JoinFailureAttribution.EXOGENOUS_PLATFORM
        if reason is JoinFailureReason.AWAITING_ADMISSION_TIMEOUT and any(
            marker in text for marker in _ADMISSION_FLOW_FAULT_MARKERS
        ):
            return JoinFailureAttribution.SYSTEM_FAULT
        return base
    except Exception:  # noqa: BLE001 — attribution must never raise into the join path
        return JoinFailureAttribution.UNKNOWN


def build_join_evidence(
    *,
    reason: JoinFailureReason,
    attribution: Optional[JoinFailureAttribution] = None,
    stage: Optional[str] = None,
    source: str = "derived",
    detail: Optional[str] = None,
    time_to_lobby_ms: Optional[int] = None,
    time_in_lobby_ms: Optional[int] = None,
    total_ms: Optional[int] = None,
    lobby_budget_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble the ``meeting.data['join_evidence']`` block.

    Only keys we actually know are emitted — an absent timing is ABSENT, never a zero, so a reader
    can tell "the bot spent 0 ms in the lobby" from "nobody measured the lobby". ``source`` names
    the producer (``bot`` / ``reconcile`` / ``runtime_destroy`` / ``derived``) so a consumer can
    weight a first-hand platform signal above a reaper's inference.

    ``attribution`` is ALWAYS emitted — the gate metric is a query over this key, and a row that
    omits it is a row the metric silently skips. Omit the argument to derive it from the reason +
    detail (:func:`attribute_join_failure`).
    """
    if attribution is None:
        attribution = attribute_join_failure(reason, detail=detail)
    evidence: Dict[str, Any] = {
        "reason": reason.value,
        "attribution": attribution.value,
        "source": source,
    }
    if stage:
        evidence["stage"] = stage
    detail_text = _clean_detail(detail)
    if detail_text:
        evidence["detail"] = detail_text
    timings: Dict[str, int] = {}
    for key, value in (
        ("time_to_lobby_ms", time_to_lobby_ms),
        ("time_in_lobby_ms", time_in_lobby_ms),
        ("total_ms", total_ms),
    ):
        ms = _positive_int(value)
        if ms is not None:
            timings[key] = ms
    if timings:
        evidence["timings"] = timings
    budget = _positive_int(lobby_budget_ms)
    if budget is not None:
        evidence["lobby_budget_ms"] = budget
    return evidence


def normalize_evidence(raw: Any, *, default_stage: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Coerce a PRODUCER-SUPPLIED ``join_evidence`` block into the canonical shape.

    The bot classifies at the source (it is the only party holding the platform signal and the
    stage timings), but the control plane never trusts a payload's shape: an unknown reason value,
    a string where a number belongs, or a hostile blob must degrade to something safe rather than
    poison the row. Unknown reasons are preserved as :attr:`JoinFailureReason.UNKNOWN` with the
    original value folded into ``detail``, so a NEWER bot's vocabulary is never silently dropped.

    An unrecognised ATTRIBUTION is discarded rather than preserved: attribution is the reliability
    gate's input, and a value this control plane cannot interpret must not be counted as anything.
    The derivation from the (normalized) reason takes over instead.

    Returns ``None`` for anything unusable — the caller then derives its own verdict.
    """
    try:
        if not isinstance(raw, Mapping):
            return None
        raw_reason = raw.get("reason")
        detail = raw.get("detail")
        try:
            reason = JoinFailureReason(raw_reason)
        except ValueError:
            reason = JoinFailureReason.UNKNOWN
            if isinstance(raw_reason, str) and raw_reason.strip():
                # A reason this control plane does not know yet (a newer bot). Keep the word: the
                # taxonomy is extensible, and an unrecognised value is information, not noise.
                detail = f"{raw_reason.strip()}: {detail}" if isinstance(detail, str) else raw_reason.strip()
        try:
            attribution: Optional[JoinFailureAttribution] = JoinFailureAttribution(raw.get("attribution"))
        except ValueError:
            attribution = None
        timings = raw.get("timings") if isinstance(raw.get("timings"), Mapping) else {}
        stage = raw.get("stage") if isinstance(raw.get("stage"), str) and raw.get("stage") else default_stage
        source = raw.get("source") if isinstance(raw.get("source"), str) and raw.get("source") else "bot"
        return build_join_evidence(
            reason=reason,
            attribution=attribution,
            stage=stage,
            source=source,
            detail=detail,
            time_to_lobby_ms=timings.get("time_to_lobby_ms"),
            time_in_lobby_ms=timings.get("time_in_lobby_ms"),
            total_ms=timings.get("total_ms"),
            lobby_budget_ms=raw.get("lobby_budget_ms"),
        )
    except Exception:  # noqa: BLE001 — a malformed payload degrades the evidence, nothing else
        return None


def evidence_from_event(
    event: Mapping[str, Any],
    *,
    stage: Optional[str] = None,
    reached_lobby: Optional[bool] = None,
    reason_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """The evidence block for one terminal ``failed`` lifecycle.v1 event.

    Prefers the producer's own typed verdict (``event['join_evidence']``, which carries the platform
    signal and the stage timings only the bot can measure) and DERIVES one otherwise — so a
    reconcile-driven terminal, a runtime-confirmed destroy, and a bot too old to send one all still
    land evidence. Returns ``None`` only when the event is unusable; never raises.
    """
    try:
        supplied = normalize_evidence(event.get("join_evidence"), default_stage=stage)
        if supplied is not None:
            return supplied
        detail = reason_text if reason_text is not None else event.get("reason")
        reason = classify_join_failure(
            completion_reason=event.get("completion_reason"),
            stage=stage,
            reached_lobby=reached_lobby,
            detail=detail,
        )
        return build_join_evidence(reason=reason, stage=stage, source="derived", detail=detail)
    except Exception:  # noqa: BLE001 — evidence capture is fail-open by contract
        return None
