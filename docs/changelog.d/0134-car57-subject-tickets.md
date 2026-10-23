- **Flows acts for a person only on that person's own event or grant.** The gateway's flows service
  door now forwards a call made for a person (reading or sharing a meeting's transcript, sending or
  stopping a bot, an autonomous turn, writing a desk, naming a room's payer, a routine's turn) only
  with a subject ticket signed by the service that produced the person's fact: meeting-api, agent-api,
  the mail intake, or the gateway for the person's own sign-in. Without one the call is refused
  `403 subject_ticket_required`. The keys are generated for you, one Ed25519 keypair per producer:
  compose's `subject-ticket-keys` one-shot, and with flows on the chart's
  `subject-ticket-{meetings,agent,mail}-key` Secrets and the `subject-ticket-verify-keys` ConfigMap.
  With `helm template` (GitOps), set `subjectTickets.<producer>.signingKey`, or
  `subjectTickets.<producer>.existingSecret` with `subjectTickets.<producer>.publicKey`.
  `secrets.existingSecretName` needs nothing new. Routines running at the upgrade are sorted once
  by agent-api: one it recorded a person turning on (from their session, or a routine file they stood
  behind that the migration converted) gets its grant and keeps running; any other is paused with
  "routines now run only on your own press — turn it on again to keep it running", and a note in its
  chat. One press of Turn on, from the person's session, starts it again. See [Flows operations](/flows/operations#subject-tickets).
