"""Lifecycle claims audit — cross-signal consistency over TERMINAL meeting rows.

A terminal row makes a claim about how a bot run ended: `completion_reason` (why a
`completed` run ended) and `failure_stage` (the furthest stage a `failed` run reached).
Those two fields are written server-side, but nothing today checks them against the
OTHER signals the same row carries — the transition trail, the captured-segment count,
and the wall-clock duration. When they disagree, the row is not merely noisy: it is a
run whose recorded outcome is false, and no failure metric can ever count it because the
row claims success.

Real shapes observed in prod (see `Vexa-ai/vexa#1191`) that this module flags:

* `completion_reason=left_alone` carried with `failure_stage=awaiting_admission` — a bot
  that never entered a meeting recorded as having been left alone in one (I1).
* `completion_reason=awaiting_admission_timeout` carried with `failure_stage=joining` (I1).
* `status=completed`, `completion_reason=left_alone`, `segments_captured=0`, duration
  well past five minutes — the bot walked out of a live meeting and recorded success (I2).

Design constraints, in order:

1. **Pure.** `audit_meeting_row` is a function of one dict. No DB, no clock, no network,
   no I/O — so CI runs it over fixtures and a nightly sweep runs the identical code over
   prod rows.
2. **Tabular.** Every invariant is expressed as data (`_REASON_STAGE_ALLOWED`,
   `_duration_envelopes`) so a new reason or a re-tuned envelope is a table edit, never a
   new branch.
3. **Parameterised, not hard-coded.** The two operational constants an envelope depends on
   — the issued admission budget and the silence window the bot leaves after — are explicit
   arguments. A checker that hard-codes them silently rots the day either is re-tuned.

The row shape is the meetings table's, tolerant of the two places prod puts the lifecycle
fields: top level, or nested under the `data` JSONB (which is where `machine.MeetingRecord.data`
projects `completion_reason` / `failure_stage` / `status_transition`). Reads go through
`_field`, which checks both.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .machine import BotStatus, CompletionReason, FailureStage, TransitionSource

__all__ = [
    "DEFAULT_ADMISSION_BUDGET_S",
    "DEFAULT_DEAF_RUN_MIN_DURATION_S",
    "DEFAULT_REJECTED_MAX_DURATION_S",
    "DEFAULT_SILENCE_WINDOW_S",
    "DEFAULT_TIMEOUT_TOLERANCE",
    "AuditParams",
    "AuditReport",
    "Finding",
    "Severity",
    "audit_meeting_row",
    "audit_rows",
    "main",
]


# ── Parameters ────────────────────────────────────────────────────────────────────────────────
# Defaults, not truths. Each mirrors an operational constant that lives elsewhere in the system;
# a sweep that runs against an environment which re-tuned one MUST pass its own value.

#: The admission budget issued to a joining bot — an `awaiting_admission_timeout` should land
#: within `DEFAULT_TIMEOUT_TOLERANCE` of it, since the timeout IS the budget expiring.
DEFAULT_ADMISSION_BUDGET_S = 900.0
#: The silence window a bot waits out before declaring itself left alone. A `left_alone` shorter
#: than this did not wait the window, so something else ended the run.
DEFAULT_SILENCE_WINDOW_S = 600.0
#: A rejection is a human (or lobby policy) saying no; it arrives fast. Longer than this and the
#: run was something other than a rejection.
DEFAULT_REJECTED_MAX_DURATION_S = 420.0
#: Fractional slack on the admission-budget envelope (spawn + teardown latency around the budget).
DEFAULT_TIMEOUT_TOLERANCE = 0.25
#: A `completed` run this long with zero captured segments is a deaf run, whatever it claims.
DEFAULT_DEAF_RUN_MIN_DURATION_S = 300.0


@dataclass(frozen=True)
class AuditParams:
    """The operational constants the envelopes are measured against."""

    admission_budget_s: float = DEFAULT_ADMISSION_BUDGET_S
    silence_window_s: float = DEFAULT_SILENCE_WINDOW_S
    rejected_max_duration_s: float = DEFAULT_REJECTED_MAX_DURATION_S
    timeout_tolerance: float = DEFAULT_TIMEOUT_TOLERANCE
    deaf_run_min_duration_s: float = DEFAULT_DEAF_RUN_MIN_DURATION_S

    def to_dict(self) -> Dict[str, float]:
        return {
            "admission_budget_s": self.admission_budget_s,
            "silence_window_s": self.silence_window_s,
            "rejected_max_duration_s": self.rejected_max_duration_s,
            "timeout_tolerance": self.timeout_tolerance,
            "deaf_run_min_duration_s": self.deaf_run_min_duration_s,
        }


# ── Findings ──────────────────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    """How load-bearing a finding is.

    `ERROR` — the row's recorded outcome contradicts its own other signals; the claim is false.
    `WARN`  — the row cannot be checked or is incompletely attributed; the claim is unverifiable.
    """

    ERROR = "error"
    WARN = "warn"


@dataclass(frozen=True)
class Finding:
    """One violated claim on one row."""

    invariant: str          # "I1".."I4"
    code: str               # stable machine id, e.g. "I1.reason_stage_mismatch"
    severity: Severity
    message: str            # human-readable, carries the contradicting values
    row_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "invariant": self.invariant,
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "row_id": self.row_id,
        }


# ── I1: reason × stage consistency ────────────────────────────────────────────────────────────
# Each `completion_reason` implies the furthest stage the run MUST have reached. A reason absent
# from this table carries no stage constraint (`stopped` is the archetype: a user stop is legal
# from any pre-terminal stage, so it constrains nothing).
_REASON_STAGE_ALLOWED: Dict[CompletionReason, frozenset[FailureStage]] = {
    # In-meeting reasons: the bot was admitted, therefore stage `active`.
    CompletionReason.LEFT_ALONE: frozenset({FailureStage.ACTIVE}),
    CompletionReason.STARTUP_ALONE: frozenset({FailureStage.ACTIVE}),
    CompletionReason.EVICTED: frozenset({FailureStage.ACTIVE}),
    # Lobby reasons: the run ended in the lobby, therefore stage `awaiting_admission`.
    CompletionReason.AWAITING_ADMISSION_TIMEOUT: frozenset({FailureStage.AWAITING_ADMISSION}),
    CompletionReason.AWAITING_ADMISSION_REJECTED: frozenset({FailureStage.AWAITING_ADMISSION}),
    # Pre-lobby reasons: the bot never got as far as the lobby.
    CompletionReason.JOIN_FAILURE: frozenset({FailureStage.REQUESTED, FailureStage.JOINING}),
}

_TERMINAL_STATUSES = frozenset({BotStatus.COMPLETED.value, BotStatus.FAILED.value})


# ── I3: duration envelopes ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Envelope:
    minimum: Optional[float]
    maximum: Optional[float]
    rationale: str

    def contains(self, seconds: float) -> bool:
        if self.minimum is not None and seconds < self.minimum:
            return False
        if self.maximum is not None and seconds > self.maximum:
            return False
        return True

    def describe(self) -> str:
        if self.minimum is not None and self.maximum is not None:
            return f"{self.minimum:.0f}s..{self.maximum:.0f}s"
        if self.minimum is not None:
            return f">= {self.minimum:.0f}s"
        return f"<= {self.maximum:.0f}s"


def _duration_envelopes(params: AuditParams) -> Dict[CompletionReason, _Envelope]:
    """The per-reason wall-clock envelope table, derived from `params`.

    Table-shaped on purpose: adding a reason is one entry, re-tuning a bound is one argument.
    """
    tolerance = params.timeout_tolerance
    return {
        CompletionReason.AWAITING_ADMISSION_REJECTED: _Envelope(
            None,
            params.rejected_max_duration_s,
            "a rejection arrives fast — a long run was not a rejection",
        ),
        CompletionReason.AWAITING_ADMISSION_TIMEOUT: _Envelope(
            params.admission_budget_s * (1.0 - tolerance),
            params.admission_budget_s * (1.0 + tolerance),
            f"the timeout IS the issued {params.admission_budget_s:.0f}s admission budget expiring "
            f"(±{tolerance:.0%})",
        ),
        CompletionReason.LEFT_ALONE: _Envelope(
            params.silence_window_s,
            None,
            f"left_alone is declared only after the {params.silence_window_s:.0f}s silence window",
        ),
    }


# ── Row reading ───────────────────────────────────────────────────────────────────────────────

def _data(row: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = row.get("data")
    return nested if isinstance(nested, Mapping) else {}


def _field(row: Mapping[str, Any], key: str) -> Any:
    """Read a lifecycle field from either the row's top level or its `data` JSONB."""
    value = row.get(key)
    if value is None:
        value = _data(row).get(key)
    return value


def _row_id(row: Mapping[str, Any]) -> Optional[str]:
    for key in ("id", "meeting_id", "connection_id", "session_uid"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _duration_s(row: Mapping[str, Any]) -> Optional[float]:
    """Explicit `duration_s` wins; otherwise derive from `created_at`/`updated_at`."""
    explicit = _field(row, "duration_s")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        return float(explicit)
    started = _parse_time(_field(row, "created_at"))
    ended = _parse_time(_field(row, "updated_at"))
    if started is None or ended is None:
        return None
    if started.tzinfo is None or ended.tzinfo is None:
        # Mixed awareness cannot be subtracted; treat as unmeasurable rather than guessing a zone.
        if (started.tzinfo is None) != (ended.tzinfo is None):
            return None
    return (ended - started).total_seconds()


def _segments(row: Mapping[str, Any]) -> Optional[int]:
    value = _field(row, "segments_captured")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _transitions(row: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    trail = _field(row, "status_transition")
    if not isinstance(trail, Sequence) or isinstance(trail, (str, bytes)):
        return []
    return [item for item in trail if isinstance(item, Mapping)]


def _transition_source(row: Mapping[str, Any]) -> Optional[str]:
    """The driver of the terminal hop — explicit column first, else the trail's last entry."""
    explicit = _field(row, "transition_source")
    if isinstance(explicit, str) and explicit:
        return explicit
    trail = _transitions(row)
    if trail:
        source = trail[-1].get("source")
        if isinstance(source, str) and source:
            return source
    return None


def _enum_or_none(enum_cls, value: Any):
    """Coerce a stored string to its enum member, or None if it is absent / unrecognised."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


# ── The invariants ────────────────────────────────────────────────────────────────────────────

def _check_i1(
    status: str,
    reason: Optional[CompletionReason],
    raw_stage: Any,
    stage: Optional[FailureStage],
) -> List[Tuple[str, Severity, str]]:
    """I1 — `completion_reason` must be consistent with `failure_stage`."""
    out: List[Tuple[str, Severity, str]] = []

    if raw_stage is not None and stage is None:
        out.append((
            "I1.unknown_failure_stage",
            Severity.WARN,
            f"failure_stage={raw_stage!r} is not a known FailureStage — the row cannot be checked",
        ))
        return out

    if stage is None:
        # A `completed` run has no failure stage by construction. A `failed` run that names a
        # reason but no stage lost its attribution somewhere between the bot and the row.
        if status == BotStatus.FAILED.value and reason is not None:
            out.append((
                "I1.missing_failure_stage",
                Severity.WARN,
                f"failed row claims completion_reason={reason.value!r} but carries no "
                f"failure_stage — the furthest stage reached was not recorded",
            ))
        return out

    allowed = _REASON_STAGE_ALLOWED.get(reason) if reason is not None else None
    if allowed is None:
        return out

    if stage not in allowed:
        expected = " or ".join(sorted(item.value for item in allowed))
        out.append((
            "I1.reason_stage_mismatch",
            Severity.ERROR,
            f"completion_reason={reason.value!r} implies failure_stage {expected}, "
            f"but the row records {stage.value!r}",
        ))
    return out


def _check_i2(
    status: str,
    segments: Optional[int],
    duration: Optional[float],
    params: AuditParams,
) -> List[Tuple[str, Severity, str]]:
    """I2 — a long `completed` run that captured nothing is a deaf run, whatever it claims."""
    if status != BotStatus.COMPLETED.value:
        return []
    if segments is None or segments > 0:
        return []
    if duration is None or duration <= params.deaf_run_min_duration_s:
        return []
    return [(
        "I2.deaf_run",
        Severity.ERROR,
        f"completed run of {duration:.0f}s captured 0 segments "
        f"(> {params.deaf_run_min_duration_s:.0f}s) — recorded as success, delivered nothing",
    )]


def _check_i3(
    reason: Optional[CompletionReason],
    duration: Optional[float],
    params: AuditParams,
) -> List[Tuple[str, Severity, str]]:
    """I3 — the run's wall clock must sit inside the envelope its reason implies."""
    if reason is None:
        return []
    envelope = _duration_envelopes(params).get(reason)
    if envelope is None:
        return []
    if duration is None:
        return [(
            "I3.duration_unmeasurable",
            Severity.WARN,
            f"completion_reason={reason.value!r} carries a duration envelope "
            f"({envelope.describe()}) but the row has neither duration_s nor a usable "
            f"created_at/updated_at pair",
        )]
    if envelope.contains(duration):
        return []
    return [(
        "I3.duration_envelope",
        Severity.ERROR,
        f"completion_reason={reason.value!r} expects {envelope.describe()} "
        f"but the run lasted {duration:.0f}s — {envelope.rationale}",
    )]


def _check_i4(
    status: str,
    raw_reason: Any,
    reason: Optional[CompletionReason],
    row: Mapping[str, Any],
) -> List[Tuple[str, Severity, str]]:
    """I4 — a terminal row names WHY it ended and WHAT drove it there. No silent jumps."""
    out: List[Tuple[str, Severity, str]] = []

    if raw_reason is None:
        out.append((
            "I4.missing_completion_reason",
            Severity.ERROR,
            f"terminal row (status={status!r}) carries no completion_reason",
        ))
    elif reason is None:
        out.append((
            "I4.unknown_completion_reason",
            Severity.WARN,
            f"completion_reason={raw_reason!r} is not a known CompletionReason",
        ))

    raw_source = _transition_source(row)
    if raw_source is None:
        out.append((
            "I4.unnamed_transition_source",
            Severity.ERROR,
            f"terminal row (status={status!r}) names no transition_source — the row arrived at "
            f"its terminal state by an unattributed jump",
        ))
    elif _enum_or_none(TransitionSource, raw_source) is None:
        out.append((
            "I4.unknown_transition_source",
            Severity.WARN,
            f"transition_source={raw_source!r} is not a known TransitionSource",
        ))

    trail = _transitions(row)
    if trail and trail[-1].get("to") != status:
        out.append((
            "I4.terminal_absent_from_trail",
            Severity.ERROR,
            f"row status is {status!r} but the status_transition trail ends at "
            f"{trail[-1].get('to')!r} — the terminal hop was never recorded",
        ))
    return out


# ── Public API ────────────────────────────────────────────────────────────────────────────────

def audit_meeting_row(
    row: Mapping[str, Any],
    *,
    params: Optional[AuditParams] = None,
    admission_budget_s: Optional[float] = None,
    silence_window_s: Optional[float] = None,
) -> List[Finding]:
    """Audit ONE meetings-table row. Pure: no I/O, no clock, no hidden state.

    Non-terminal rows are not audited — a run still in flight has not made a claim yet — and
    return an empty list.

    `admission_budget_s` and `silence_window_s` are surfaced as direct keyword arguments because
    they are the two envelope inputs a caller most often has to override per environment; pass a
    full `AuditParams` to set the rest.
    """
    resolved = params or AuditParams()
    if admission_budget_s is not None or silence_window_s is not None:
        resolved = AuditParams(
            admission_budget_s=(
                admission_budget_s if admission_budget_s is not None
                else resolved.admission_budget_s
            ),
            silence_window_s=(
                silence_window_s if silence_window_s is not None else resolved.silence_window_s
            ),
            rejected_max_duration_s=resolved.rejected_max_duration_s,
            timeout_tolerance=resolved.timeout_tolerance,
            deaf_run_min_duration_s=resolved.deaf_run_min_duration_s,
        )

    status = _field(row, "status")
    if not isinstance(status, str) or status not in _TERMINAL_STATUSES:
        return []

    raw_reason = _field(row, "completion_reason")
    raw_stage = _field(row, "failure_stage")
    reason = _enum_or_none(CompletionReason, raw_reason)
    stage = _enum_or_none(FailureStage, raw_stage)
    duration = _duration_s(row)
    segments = _segments(row)

    raw: List[Tuple[str, Severity, str]] = []
    raw += [(code, sev, msg) for code, sev, msg in _check_i1(status, reason, raw_stage, stage)]
    raw += _check_i2(status, segments, duration, resolved)
    raw += _check_i3(reason, duration, resolved)
    raw += _check_i4(status, raw_reason, reason, row)

    row_id = _row_id(row)
    return [
        Finding(
            invariant=code.split(".", 1)[0],
            code=code,
            severity=severity,
            message=message,
            row_id=row_id,
        )
        for code, severity, message in raw
    ]


@dataclass
class AuditReport:
    """The result of sweeping a batch of rows."""

    rows_seen: int = 0
    rows_audited: int = 0
    rows_skipped: int = 0
    findings: List[Finding] = field(default_factory=list)
    params: AuditParams = field(default_factory=AuditParams)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def rows_flagged(self) -> int:
        return len({finding.row_id for finding in self.findings})

    def by_invariant(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for finding in self.findings:
            counts[finding.invariant] = counts.get(finding.invariant, 0) + 1
        return dict(sorted(counts.items()))

    def by_code(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code] = counts.get(finding.code, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows_seen": self.rows_seen,
            "rows_audited": self.rows_audited,
            "rows_skipped": self.rows_skipped,
            "rows_flagged": self.rows_flagged,
            "by_invariant": self.by_invariant(),
            "by_code": self.by_code(),
            "params": self.params.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def render(self) -> str:
        lines = [
            "lifecycle claims audit",
            f"  rows seen      : {self.rows_seen}",
            f"  rows audited   : {self.rows_audited} (terminal)",
            f"  rows skipped   : {self.rows_skipped} (non-terminal)",
            f"  rows flagged   : {self.rows_flagged}",
            f"  findings       : {len(self.findings)}",
        ]
        counts = self.by_invariant()
        if counts:
            lines.append("  by invariant   : " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        lines.append("  params         : " + ", ".join(
            f"{k}={v:g}" for k, v in self.params.to_dict().items()
        ))
        if self.findings:
            lines.append("")
            for finding in self.findings:
                lines.append(
                    f"  [{finding.severity.value.upper():5}] {finding.code} "
                    f"row={finding.row_id or '<unidentified>'}"
                )
                lines.append(f"          {finding.message}")
        else:
            lines.append("")
            lines.append("  no findings — every terminal row's claim is consistent with its signals")
        return "\n".join(lines)


def audit_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    params: Optional[AuditParams] = None,
    admission_budget_s: Optional[float] = None,
    silence_window_s: Optional[float] = None,
) -> AuditReport:
    """Audit an iterable of rows into one report. Pure, and streams (never materialises the input)."""
    resolved = params or AuditParams()
    report = AuditReport(params=resolved)
    for row in rows:
        report.rows_seen += 1
        status = _field(row, "status") if isinstance(row, Mapping) else None
        terminal = isinstance(status, str) and status in _TERMINAL_STATUSES
        if terminal:
            report.rows_audited += 1
        else:
            report.rows_skipped += 1
            continue
        report.findings.extend(
            audit_meeting_row(
                row,
                params=resolved,
                admission_budget_s=admission_budget_s,
                silence_window_s=silence_window_s,
            )
        )
    # `params` on the report must reflect what was actually applied, overrides included.
    if admission_budget_s is not None or silence_window_s is not None:
        report.params = AuditParams(
            admission_budget_s=(
                admission_budget_s if admission_budget_s is not None
                else resolved.admission_budget_s
            ),
            silence_window_s=(
                silence_window_s if silence_window_s is not None else resolved.silence_window_s
            ),
            rejected_max_duration_s=resolved.rejected_max_duration_s,
            timeout_tolerance=resolved.timeout_tolerance,
            deaf_run_min_duration_s=resolved.deaf_run_min_duration_s,
        )
    return report


# ── Entrypoint ────────────────────────────────────────────────────────────────────────────────
# `python -m meeting_api.lifecycle.claims_audit <rows.json>` — the shape a nightly sweep wraps.
# Deploying that sweep is out of scope here; this is the checker it will call.

def load_rows(payload: Any) -> List[Mapping[str, Any]]:
    """Accept either a bare JSON array of rows or `{"rows": [...]}`."""
    if isinstance(payload, Mapping):
        payload = payload.get("rows")
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise ValueError("expected a JSON array of meeting rows, or an object with a `rows` array")
    return [row for row in payload if isinstance(row, Mapping)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m meeting_api.lifecycle.claims_audit",
        description=(
            "Audit terminal meeting rows for claims that contradict their own other signals. "
            "Exits 1 when anything is flagged."
        ),
    )
    parser.add_argument("rows", help="path to a JSON file of meeting rows, or - for stdin")
    parser.add_argument(
        "--admission-budget-s", type=float, default=DEFAULT_ADMISSION_BUDGET_S,
        help="issued lobby admission budget (default: %(default)s)",
    )
    parser.add_argument(
        "--silence-window-s", type=float, default=DEFAULT_SILENCE_WINDOW_S,
        help="silence window before left_alone is declared (default: %(default)s)",
    )
    parser.add_argument(
        "--rejected-max-duration-s", type=float, default=DEFAULT_REJECTED_MAX_DURATION_S,
        help="longest plausible rejected run (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout-tolerance", type=float, default=DEFAULT_TIMEOUT_TOLERANCE,
        help="fractional slack on the admission-budget envelope (default: %(default)s)",
    )
    parser.add_argument(
        "--deaf-run-min-duration-s", type=float, default=DEFAULT_DEAF_RUN_MIN_DURATION_S,
        help="shortest completed run that counts as deaf when it captured nothing "
             "(default: %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        if args.rows == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.rows, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        rows = load_rows(payload)
    except (OSError, ValueError) as exc:
        print(f"claims_audit: cannot read rows: {exc}", file=sys.stderr)
        return 2

    report = audit_rows(
        rows,
        params=AuditParams(
            admission_budget_s=args.admission_budget_s,
            silence_window_s=args.silence_window_s,
            rejected_max_duration_s=args.rejected_max_duration_s,
            timeout_tolerance=args.timeout_tolerance,
            deaf_run_min_duration_s=args.deaf_run_min_duration_s,
        ),
    )
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI wiring
    raise SystemExit(main())
