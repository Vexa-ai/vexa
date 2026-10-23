"""Subject tickets, as the engine carries them — opaque strings it never reads.

A producer that emits a fact about a person attaches a signed ticket (`subject-ticket.v1`) under
the refs key `_tickets`. The engine does not verify, parse or mint one; it only keeps them with the
fact so the door call a step makes for that person can present them:

* admission keeps `refs["_tickets"]` as delivered, and a second delivery of the same fact UNIONS
  its tickets into the admitted reaction (`admission._merge_refs`);
* a fact a step emits inherits its parent reaction's tickets (`loop._with_origin`);
* a keyed fire's payload is the arming event's refs, tickets included;
* a person's flow presents the routine grant stored with its version on every run;
* while a step runs, `current` holds the running reaction's tickets, and the door helper
  (`flows_steps.common.door_headers`) sends them. flows-api sets the same variable per request.

`current` holds a LIST, mutated in place by `add`: a value set inside a copied context (a FastAPI
dependency run in a worker thread) then reaches the code that reads it in another copy of the
same request's context. Engine core: stdlib only.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterable, Iterator, Optional

REFS_KEY = "_tickets"

#: The tickets the code running now may present on a door call; None outside a step or request.
current: ContextVar[Optional[list]] = ContextVar("flows_subject_tickets", default=None)


def of(refs: Any) -> list:
    """The tickets a reaction's refs carry, as a clean list."""
    raw = refs.get(REFS_KEY) if isinstance(refs, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [t.strip() for t in raw if isinstance(t, str) and t.strip()]


def union(*lists: Optional[Iterable[Any]]) -> list:
    """Several ticket lists as one: first-seen order, blanks and duplicates dropped."""
    seen: set = set()
    out: list = []
    for lst in lists:
        for t in lst or ():
            s = str(t or "").strip() if isinstance(t, str) else ""
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


@contextmanager
def presenting(tickets: Optional[Iterable[Any]]) -> Iterator[list]:
    """Make `tickets` the ones a door call presents for the duration of the block."""
    token = current.set(union(tickets))
    try:
        yield current.get()
    finally:
        current.reset(token)


def add(tickets: Optional[Iterable[Any]]) -> None:
    """Add to the tickets being presented now (no-op outside `presenting`)."""
    held = current.get()
    if held is None:
        return
    for t in union(tickets):
        if t not in held:
            held.append(t)


def presented() -> list:
    """The tickets a door call made now presents."""
    return list(current.get() or [])
