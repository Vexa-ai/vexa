# home-customer.v1 — what Vexa knows about a person as a customer — UNSEALED

Kept beside every home composition (`home-card.v1` is the other half, the rendered `view`) and never
drawn as cards. Read by the person's own agent through `customer_profile` and by the next
composition, which updates it from new evidence and outcomes and records what changed and why
(`changes`). Every field carries its evidence ids, a confidence and when it changed. The person can
see it (ask the agent "what does Vexa know about me?") and correct any field with `customer_correct`;
a correction wins until they correct it again. Ids and short summaries only. Writer:
`core/agent/control_plane/home.py` (`update_customer`).
