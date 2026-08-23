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

    def flow(self, *, name: str, version: int, on: EventType, steps: list[StepFn]) -> Flow:
        for fn in steps:
            if self.steps.get(fn.__name__) is not fn:
                raise ValueError(f"unregistered step in flow {name}: {fn.__name__}")
        f = Flow(name=name, version=version, on=on, steps=tuple(fn.__name__ for fn in steps))
        self.flows[(name, version)] = f
        self.by_event.setdefault(on.name, []).append(f)
        return f

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
