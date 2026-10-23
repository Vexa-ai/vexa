# subject-ticket.v1 — the producer's signature on who a flows call is for

The flows tier reaches the other domains only through the gateway's **service door**
(`service_door.py`), with one credential (`VEXA_FLOWS_SERVICE_KEY`). On the calls that act **for a
person**, flows names that person (`X-User-Id`, or a body field such as `bills_to`). A name is a
claim anybody holding the flows key can make. A **subject ticket** is the producer's signature over
it: the service that emitted the person's fact signs WHO the fact is about, WHICH calls it lets
flows make for them, and until WHEN.

```
X-Vexa-Subject-Ticket: st1.<b64u(claims)>.<b64u(Ed25519(issuer's private key, "st1." + b64u(claims)))>[,<another>…]
```

`claims` is JSON with sorted keys and no whitespace (`#/$defs/Claims`). The canonical
implementation is [`subject_ticket.py`](subject_ticket.py): standard library only, the signer and
the checks injected. It is vendored byte for byte into the door (gateway), the producers
(meeting-api, agent-api, the flows mail intake) and flows (which only carries tickets);
`gate:fact-parity` (`subject-ticket`) holds the copies equal.

## Who signs, who verifies

| Issuer | Signed by | Key (private, held only by the signer) | Ticket |
|---|---|---|---|
| `meetings` | meeting-api | `VEXA_SUBJECT_TICKET_KEY_FILE` | on `meeting.started` / `meeting.completed` / `meeting.shared`: `sub` = the meeting's owner, `mid` = the meeting, `native` = `<platform>/<native id>` |
| `agent` | agent-api | `VEXA_SUBJECT_TICKET_KEY_FILE` | a routine **grant** when the person turns a routine on from their own session (`evt` = `user:<uid>/<name>`); an event ticket on a message to a person's chat |
| `mail` | the flows mail intake (flows-mailbox) and the worker's mailbox sensor | `VEXA_FLOWS_MAIL_TICKET_KEY_FILE` | an authenticated invite (`addr` = the DMARC-authenticated organizer, `native` = its meeting, `peers` = its attendees, `once` = `bot.dispatch`) or reply |
| `gateway` | the gateway (its identity key) | `VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE` | answered on a successful `/identity/internal/validate` relay: the person's own bearer, presented to flows-api |

**The `mail` key is held by two processes, not one.** Mail is read in two places: the flows-mailbox
lane (the deployment's mailbox) and the `mailbox` sensor kind (ADR-0050), which runs in
flows-worker's sensor pool. Each signs in the same step that authenticated the message (the DMARC
verdict, the organizer, the allow-list), so both mount `VEXA_FLOWS_MAIL_TICKET_KEY_FILE`; flows-api
never does (compose and chart tests hold this). Confining the key to flows-mailbox would need a
signing channel from the worker to it, and flows-mailbox serves no port: that is a new trust seam,
not a mount change. The `mail` issuer's key is therefore exactly as confined as flows-worker, which
also runs every flow's steps.

**The verifier is the door.** It holds the producers' PUBLIC keys only, one SubjectPublicKeyInfo
PEM per issuer in `VEXA_SUBJECT_TICKET_KEYS_DIR/<issuer>.pem` (`meetings.pem`, `agent.pem`,
`mail.pem`), and verifies `gateway` tickets with its own key's public half. Nothing the door, flows
or any other producer holds can sign for `meetings`, `agent` or `mail`. Flows carries tickets
(`refs._tickets`; admission keeps them, a merge unions them, a child fact inherits them, a flow
presents its routine grant on every run) and cannot mint one.

## Issuer policy (enforced at mint and at read)

| Issuer | May grant | Kinds and longest lifetime |
|---|---|---|
| `meetings` | `transcript.read`, `chat.autonomous`, `desk.write`, `room.bills_to` | event 3 d |
| `agent` | `routine.turn`, `bot.dispatch` | event 1 d · grant 400 d |
| `mail` | `bot.dispatch`, `transcript.read`, `chat.autonomous`, `desk.write` | event 60 d |
| `gateway` | `desk.write`, `routine.turn`, `bot.dispatch` | event 600 s · grant 400 d |

A ticket listing a class its issuer may not grant, or living longer than its (issuer, kind) cap, is
refused (`issuer_not_allowed`, `lifetime_too_long`) even when correctly signed.

## The door's check

A bound call passes only if one presented ticket (at most 16 per call):

1. verifies under its issuer's public key (signature first, before any claim is believed);
2. is unexpired (`iat` at most 60 s in the future, `now < exp`);
3. lists the call's class, and its issuer may grant it;
4. names the call's subject: `sub` equal, or `addr` equal to the subject's address, or (for
   `desk.write` only) the subject's address in `peers`;
5. addresses the call's meeting where the call addresses one (`mid`, `native`);
6. for a class in `once`, has not been used successfully before (the `jti` is recorded on a 2xx).

Otherwise **403** `subject_ticket_required` (none presented), `subject_ticket_refused` (with a
reason per ticket: `malformed`, `unknown_issuer`, `bad_signature`, `lifetime_too_long`,
`not_yet_valid`, `expired`, `issuer_not_allowed`, or a subject/class/meeting mismatch) or
`ticket_replayed`. System calls (lookups, validate, settings reads) name no person and stay unbound.

## Keys at install

Every surface generates one keypair per producer, keeps it across upgrades, and gives each private
key to its producer alone and the public halves to the gateway alone: compose's
`subject-ticket-keys` one-shot, the Helm chart's `subject-ticket-keys.yaml` (a Secret per producer,
the `subject-ticket-verify-keys` ConfigMap; `subjectTickets.<issuer>.signingKey`, or
`existingSecret` + `publicKey`). Lite runs no flows tier and needs none. A producer with no key
still delivers its facts, without a ticket, and logs once that the door will refuse what they cause.

## Goldens

[`golden/`](golden/), written by [`make_goldens.py`](make_goldens.py) from the canonical module
(fixed `now`, pinned `jti`, the RFC 8032 section 7.1 TEST 1 key) and checked by
[`validate.mjs`](validate.mjs) in Node: each vector's payload is re-serialized and re-signed byte
for byte and held to the issuer policy; each refusal is refused for its stated reason by the
verifier's rules restated there.
