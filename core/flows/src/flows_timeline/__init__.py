"""flows_timeline — one person's day, in order (PRD decision 31).

`model` is pure and takes rows; `service` reads the flows database and the meetings table. Kept
out of `flows/` because it is not the engine (it never admits, claims or runs anything) and out of
`flows_steps/` because it is not a step (nothing in a flow calls it) — it is a READ over what those
two already wrote.
"""
from __future__ import annotations

from flows_timeline.model import (EVENT_KINDS, STEP_KINDS, Event, concerns, event_from_meeting,
                                  event_from_receipt, events_from_reaction, iso, merge,
                                  split_around, to_epoch)
from flows_timeline.render import render_preamble, render_text
from flows_timeline.service import (NO_SESSION, REACTION_FOUND, REACTION_MISSING,
                                    REACTION_NOT_YOURS, build_timeline, fetch_meetings,
                                    friction_for_subject, list_reactions, read_flows,
                                    reaction_concerns, resolve_identity, window)

__all__ = ["EVENT_KINDS", "STEP_KINDS", "Event", "concerns", "event_from_meeting",
           "event_from_receipt", "events_from_reaction", "iso", "merge", "split_around",
           "to_epoch", "build_timeline", "fetch_meetings", "friction_for_subject", "NO_SESSION",
           "list_reactions", "read_flows",
           "reaction_concerns", "REACTION_FOUND", "REACTION_MISSING", "REACTION_NOT_YOURS",
           "resolve_identity",
           "window", "render_preamble", "render_text"]
