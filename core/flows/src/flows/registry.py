"""Flows are DATA: a typed event class + an ordered list of step FUNCTIONS.
Strings exist only in the database — derived here from __name__, never authored."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import logging

from .model import Block, Done, StepCtx, Wait

_log = logging.getLogger(__name__)

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
        # WHICH FLOWS CAME FROM THE DATABASE. Needed only to answer "is a runtime-authored version
        # shadowing the code's?" — see `shadowing_versions`. Without it the two are
        # indistinguishable here, which is precisely why the shadow was invisible.
        self.db_versions: set[tuple[str, int]] = set()

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
        self.db_versions.add((name, version))
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
        for s in self.shadowing_versions():
            _log.warning(
                "FLOW SHADOW: %s@%d (authored through the API) is ACTIVE and NEWER than the "
                "image's %s@%d, and omits %s — %s will never run for a new %s. A code change that "
                "adds a step to this flow is inert until a higher version including it is "
                "submitted. DB steps=%s · code steps=%s",
                s["flow"], s["active_db_version"], s["flow"], s["shadowed_code_version"],
                ", ".join(s["steps_that_never_run"]), ", ".join(s["steps_that_never_run"]),
                s["flow"], s["db_steps"], s["code_steps"])
        return n

    def shadowing_versions(self) -> list[dict]:
        """Runtime-authored flow versions that SHADOW the code's newest version of the same flow
        with a SMALLER step list — i.e. steps the image defines that will never run.

        This exists because it happened and cost a day. `post_meeting@2` was submitted through the
        API during a walkthrough with four steps; the image's `post_meeting@1` had five, the fifth
        being `drop_to_attendees` — the step that puts a meeting's record on every attendee's desk.
        `match()` is newest-wins, so the four-step version governed every meeting from that moment,
        the fifth step never ran, and NOTHING SAID SO: no error, no warning, and the code kept
        reading as though it were live. A code change that adds a step to a flow is silently inert
        while any higher DB version exists, and there was no way to see it short of diffing the
        table against the source by hand.

        Reported as a WARNING at every refresh and exposed through `flows_list`, because the shape
        of this defect is that nobody goes looking."""
        out: list[dict] = []
        for name in sorted({n for n, _ in self.flows}):
            code = [f for (n, v), f in self.flows.items()
                    if n == name and (n, v) not in self.db_versions]
            db = [f for (n, v), f in self.flows.items()
                  if n == name and (n, v) in self.db_versions]
            if not code or not db:
                continue
            top_code = max(code, key=lambda f: f.version)
            top_db = max(db, key=lambda f: f.version)
            if top_db.version <= top_code.version:
                continue
            missing = [s for s in top_code.steps if s not in top_db.steps]
            if missing:
                out.append({"flow": name, "shadowed_code_version": top_code.version,
                            "active_db_version": top_db.version, "steps_that_never_run": missing,
                            "code_steps": list(top_code.steps), "db_steps": list(top_db.steps)})
        return out

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
