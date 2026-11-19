# flows_delivery

The nudge delivery gate (S6, D15): `gate.py` decides whether a waiting card's nudge goes out (seen suppresses, idle window, cap, working hours), `templates.py` composes template-only bodies (the floor: no message text, no private field), `store.py` keeps the delivery rows and receipts, `ports.py` the doors it reaches (agent-api notices, crm cards, mail), `run.py` the scheduled step. Off unless `VEXA_FLOWS_NUDGES=1` and the `flows_defs.nudges` pack is loaded.
