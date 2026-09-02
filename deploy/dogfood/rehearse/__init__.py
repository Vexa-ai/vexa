"""rehearse — user states as data (PRD decision 38).

Enter any state a person can be in, on the RUNNING stack, without a rebuild:

    from rehearse import load, LiveDoors, rehearse
    rehearse("organizer-invited", "olga@rehearse.test", doors=LiveDoors())

Four files and one rule. `states.yaml` is the catalogue — the recipes, as data. `catalogue.py`
validates them against a closed vocabulary. `doors.py` is the only thing that talks to the stack,
one method per verb. `engine.py` executes a recipe and verifies its artefacts. The rule is that a
state is entered through the product's own doors: no DB writes, no volume edits, no image.

`run_all.py` runs the whole catalogue and files what breaks as friction (decision 33).
"""
from .catalogue import Catalogue, CatalogueError, State, Step, load  # noqa: F401
from .doors import DoorRefused, Doors, LiveDoors                     # noqa: F401
from .engine import Refused, Result, rehearse, subject_reset         # noqa: F401

__all__ = ["Catalogue", "CatalogueError", "State", "Step", "load", "Doors", "LiveDoors",
           "DoorRefused", "rehearse", "subject_reset", "Result", "Refused"]
