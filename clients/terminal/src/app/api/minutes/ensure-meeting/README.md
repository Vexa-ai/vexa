# /api/minutes/ensure-meeting — the door never lands on a void (dev seam)

Finds-or-creates the signed-in user's row for the meeting a door link names, through the public
gateway with a per-user token — the same call the mailroom makes. 404s outside local-dev minutes
mode; in prod the mailroom creates rows for every resolved participant at invite time.
