"""The sensor interface (see the package docstring). Pure data and one abstract method."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


def scope_class(scope: str) -> str:
    """`user` for a person's scope (`user:<uid>`), `instance` for the company's, else the scope."""
    return "user" if (scope or "").startswith("user:") else (scope or "")


@dataclass(frozen=True)
class Change:
    """One thing that changed in the world, as a typed event. `dedupe_key` names the change itself
    (an event id plus its etag, a message id), so the same change read twice is admitted once.
    A per-person sensor's pool stamps `uid` (the owner) on `refs`; an instance sensor's change must
    name the person it is about as `refs["uid"]` itself, or it reaches only instance flows."""
    event_type: str
    dedupe_key: str
    refs: dict = field(default_factory=dict)


@dataclass
class Tick:
    """What one read found: the changes, the next cursor (None = unchanged), and whether the cursor
    went stale and must be dropped (the provider answered 410 / invalid cursor)."""
    changes: list = field(default_factory=list)
    cursor: Optional[str] = None
    resync: bool = False


class SensorFault(Exception):
    """A tick that failed. `reason` is safe to show the person (it names the failure class, never a
    credential); `retryable=False` stops the sensor until something changes (e.g. disconnected)."""

    def __init__(self, reason: str, *, retryable: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass
class SensorRow:
    """A sensor as its owner made it and the pool holds it (the `sensor` table)."""
    scope: str                       # `user:<uid>` or `instance` (the company's, the global account's)
    name: str
    kind: str
    connection_id: Optional[str] = None
    filter: Optional[str] = None     # flows.triggers filter over the event envelope, applied before emit
    interval_s: float = 60.0
    version: int = 1
    tested_version: Optional[int] = None
    cursor: Optional[str] = None
    status: str = "draft"
    mode: str = "poll"
    failures: int = 0
    reason: Optional[str] = None
    last_tick_at: Optional[float] = None
    last_change_at: Optional[float] = None
    next_due_at: float = 0.0

    @property
    def sensor_id(self) -> str:
        return f"{self.scope}/{self.name}"

    @property
    def uid(self) -> str:
        """The owner's uid, "" for the company's."""
        return self.scope[len("user:"):] if self.scope.startswith("user:") else ""


class SensorKind:
    """A building block a person's sensor is made of. Subclass, set the attributes, implement `tick`."""
    kind: str = ""
    #: the event types this kind emits (a connected chat or a trigger listens on them)
    event_types: tuple = ()
    #: the shortest interval a sensor of this kind may poll at, whatever its owner asks
    min_interval_s: float = 60.0
    #: failures in a row before the sensor reads `degraded`
    degrade_after: int = 3
    #: what the kind needs to read: "google_calendar", "google_email", "" (nothing)
    connection: str = ""
    #: the scopes a sensor of this kind may live in: "user" (a person's own) and/or "instance"
    scopes: tuple = ("user", "instance")

    def allowed_in(self, scope: str) -> bool:
        """Whether a sensor of this kind may live in `scope` (`user:<uid>` or `instance`)."""
        return scope_class(scope) in tuple(self.scopes or ())

    def tick(self, sensor: SensorRow, cursor: Optional[str]) -> Tick:  # pragma: no cover — abstract
        raise NotImplementedError
