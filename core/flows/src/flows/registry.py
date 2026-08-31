"""Flows are DATA: a typed event class + an ordered list of step FUNCTIONS.
Strings exist only in the database — derived here from __name__, never authored."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .model import Block, Done, StepCtx, Wait

StepFn = Callable[[StepCtx], "Done | Wait | Block"]


@dataclass(frozen=True)
class EventType:
    """The typed trigger. `name` is the sealed envelope's event_type string."""
    name: str


@dataclass(frozen=True)
class Flow:
    name: str
    version: int
    on: EventType
    steps: tuple[str, ...]                # names, derived — the DB representation
    params: tuple = ()                    # (key, json-value-str) pairs — flow-as-data tuning

    def param(self, key: str, default=None):
        import json as _j
        for k, v in self.params:
            if k == key:
                return _j.loads(v)
        return default

    def next_step(self, current: str) -> Optional[str]:
        i = self.steps.index(current)
        return self.steps[i + 1] if i + 1 < len(self.steps) else None


class Registry:
    """flow name+version → Flow · step name → fn · event type → flows to start.
    Built at composition time from VALUES (functions, Flow objects) — a typo is an
    error at registration, not a KeyError mid-reaction."""

    def __init__(self) -> None:
        self.flows: dict[tuple[str, int], Flow] = {}
        self.steps: dict[str, StepFn] = {}
        self.by_event: dict[str, list[Flow]] = {}

    def step(self, fn: StepFn) -> StepFn:
        name = fn.__name__
        if name in self.steps and self.steps[name] is not fn:
            raise ValueError(f"step name already registered: {name}")
        self.steps[name] = fn
        return fn

    def flow(self, *, name: str, version: int, on: EventType, steps: list[StepFn],
             params: dict | None = None) -> Flow:
        for fn in steps:
            if self.steps.get(fn.__name__) is not fn:
                raise ValueError(f"unregistered step in flow {name}: {fn.__name__}")
        import json as _j
        f = Flow(name=name, version=version, on=on, steps=tuple(fn.__name__ for fn in steps),
                 params=tuple((k, _j.dumps(v)) for k, v in (params or {}).items()))
        self.flows[(name, version)] = f
        self.by_event.setdefault(on.name, []).append(f)
        return f

    def flow_by_names(self, *, name: str, version: int, on_event: str, step_names: list[str],
                      params: dict | None = None) -> Flow:
        """Hydrate a DB-DEFINED flow: steps referenced by NAME against the image's reviewed
        vocabulary. Unknown names raise — submission-time validation mirrors this."""
        missing = [n for n in step_names if n not in self.steps]
        if missing:
            raise ValueError(f"unknown steps {missing}; known: {sorted(self.steps)}")
        import json as _j
        f = Flow(name=name, version=version, on=EventType(on_event), steps=tuple(step_names),
                 params=tuple((k, _j.dumps(v)) for k, v in (params or {}).items()))
        self.flows[(name, version)] = f
        bucket = self.by_event.setdefault(on_event, [])
        bucket[:] = [x for x in bucket if not (x.name == name and x.version == version)] + [f]
        return f

    def refresh_from_db(self, db) -> int:
        """Load ACTIVE flow rows — DB versions join (and, at higher versions, supersede via the
        newest-wins matcher) the code-registered ones. Called periodically by the worker: a
        submitted flow is live within one refresh, no image rebuild."""
        import json as _j
        n = 0
        try:
            rows = db.execute("SELECT name, version, on_event, steps, params FROM flow_version "
                              "WHERE status = 'active'")
        except Exception:  # noqa: BLE001 — a missing table (tests on bare sqlite) is fine
            return 0
        for name, version, on_event, steps, params in rows:
            if (name, version) in self.flows:
                continue
            try:
                self.flow_by_names(name=name, version=version, on_event=on_event,
                                   step_names=_j.loads(steps), params=_j.loads(params or "{}"))
                n += 1
            except ValueError:
                continue          # a row referencing steps this image lacks stays dormant here
        return n

    def get(self, name: str, version: int) -> Flow:
        return self.flows[(name, version)]

    def match(self, event_type: str) -> list[Flow]:
        """One version per flow IDENTITY: new events select the newest activated version of each
        flow (in-flight reactions keep the version stamped at their admission). Caught by the
        version-coexistence test: returning every registered version made v1 shadow v2 forever."""
        latest: dict[str, Flow] = {}
        for f in self.by_event.get(event_type, []):
            cur = latest.get(f.name)
            if cur is None or f.version > cur.version:
                latest[f.name] = f
        return list(latest.values())
