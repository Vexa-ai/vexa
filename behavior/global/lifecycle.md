---
kind: lifecycle
read_by: agent-api home (control_plane/home_playbook.py), on every home composition
curriculum: onboarding.md
---

# Lifecycle

The organizing axis of the new-chat home: which stage each person is in, decided from their usage,
and what their home is for at that stage. The home reads this file on every composition; edit it and
the next one follows. Stages are checked **from the top down**: a person is in the first stage whose
`enter when` all hold (the last stage has none, so everybody lands somewhere). Every change of stage
is recorded, with its evidence, in what Vexa knows about that person as a customer.

Each stage is a `## <id>` heading with these lines:

- `enter when` — evidence, comma-separated, all must hold: `<fact> >= N` or `<fact> < N`. Facts:
  `meetings`, `meetings_7d`, `chats`, `routines`, `shared_chats`, `workspaces`, `mentions`,
  `voice_notes`, `connected` (calendar and mail sources ready), `age_days`, `idle_days`
- `goal` — what the home is trying to achieve for someone here
- `weights` — the home's purpose mix: `work`, `people`, `automation`, `connections`, `teaching`
  (any non-negative numbers; they are used relative to each other)
- `curriculum` — the steps of [`onboarding.md`](onboarding.md) in scope here, comma-separated, or `all`
- `nudges` — caps per composition, e.g. `teaching 2, connections 1, automation 1`
- `success` — the evidence that moves someone on, in words for the person reading this file

## dormant
- enter when: idle_days >= 21
- goal: one short "since you left" and one reason to come back
- weights: work 1, people 0.8, automation 0.1, connections 0.1, teaching 0.2
- curriculum: none
- nudges: teaching 1, connections 0, automation 0
- success: a visit and one act from the home

## drifting
- enter when: idle_days >= 7
- goal: show what moved while they were away, and what is waiting on them
- weights: work 1, people 0.8, automation 0.2, connections 0.2, teaching 0.3
- curriculum: none
- nudges: teaching 1, connections 1, automation 0
- success: a visit and one act from the home

## power
- enter when: routines >= 1, meetings >= 20
- goal: high-leverage moves — automate what repeats, bring colleagues in
- weights: work 1, people 0.6, automation 0.6, connections 0.2, teaching 0.15
- curriculum: mention, crm-record
- nudges: teaching 1, connections 1, automation 2
- success: stays here; a second routine or a shared workspace counts as depth

## active
- enter when: meetings >= 5
- goal: get the work done, and try what saves time next
- weights: work 1, people 0.5, automation 0.4, connections 0.3, teaching 0.3
- curriculum: voice-note, workspace, share-chat, routine
- nudges: teaching 2, connections 1, automation 1
- success: a routine running, or a chat shared with a colleague

## activating
- enter when: meetings >= 1
- goal: the first real work, and the sources that make it useful
- weights: work 1, people 0.4, automation 0.1, connections 0.6, teaching 0.6
- curriculum: connect-calendar, connect-mail, voice-note, workspace
- nudges: teaching 2, connections 1, automation 0
- success: five meetings, or calendar and mail connected

## new
- goal: see Vexa transcribe something, and connect a calendar
- weights: work 0.6, people 0.3, automation 0, connections 0.8, teaching 1
- curriculum: connect-calendar, first-meeting, connect-mail
- nudges: teaching 2, connections 1, automation 0
- success: one meeting transcribed
