# subject-ticket.v1 goldens

Written by `../make_goldens.py` from the canonical `subject_ticket.py` (`mint` at `now`
1767225600 with a pinned `jti`; every refusal checked against `read` first). Re-running it
reproduces every file byte for byte (Ed25519 is deterministic).

- `vector-meetings-event.json` — meeting.completed for the meeting's owner: `mid`, `native`, the
  four meetings classes, 3 days.
- `vector-agent-grant.json` — a routine the person turned on: a 400-day `grant` for
  `routine.turn` and `bot.dispatch`.
- `vector-mail-invite.json` — an authenticated invite: `addr` (lower-cased), `native`, `peers`,
  `once: [bot.dispatch]`.
- `vector-gateway-event.json` — the person's own bearer, validated through the door: 600 s.
- `refused-*.json` — what a verifier holding `public_key` for meetings, agent, mail and gateway
  must refuse at `now`, and why: another key, a changed subject, no signature, another scheme name
  (`bad_signature` / `malformed`); past `exp`, more than 60 s before `iat`, a gateway event over
  600 s, an agent ticket opening `transcript.read` (`expired` / `not_yet_valid` /
  `lifetime_too_long` / `issuer_not_allowed`, each correctly signed); an issuer it holds no key
  for (`unknown_issuer`).

Signing vectors use the RFC 8032 section 7.1 TEST 1 key; no golden carries a private key, and
`validate.mjs` derives it from the seed the RFC publishes. It is a published test key, so no
deployment may use it.
