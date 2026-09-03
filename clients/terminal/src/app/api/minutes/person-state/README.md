# /api/minutes/person-state — server-side once-ever flags (dev seam)

Per-person flags in `.system/<uid>/minutes-state.json` — the guard for flows that must fire once
per PERSON, not once per browser (door kickoff, setup). 404s outside local-dev minutes mode; the
production home for this state is the private system workspace itself.
