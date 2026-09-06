---
kind: flow-index
flows: 11
generated: from the code that runs them — edits here are overwritten
---

# Flows

Everything this deployment does on its own, one page each. A flow is a trigger and an ordered list of steps; every page below is written from the code that runs it, down to the Python at its foot.

| flow | runs when | steps | rules it honours |
|---|---|---|---|
| [`desk_claim`](desk_claim.md) | `claim.proposed` | 1 | — |
| [`desk_setup`](desk_setup.md) | `desk.unscaffolded` | 1 | — |
| [`email_chat`](email_chat.md) | `mail.reply` | 2 | — |
| [`friction_fix`](friction_fix.md) | `friction.fixed` | 1 | — |
| [`friction_log`](friction_log.md) | `friction.reported` | 1 | — |
| [`invite_intake`](invite_intake.md) | `invite.received` | 9 | — |
| [`live_meeting`](live_meeting.md) | `meeting.started` | 1 | — |
| [`meeting_prep`](meeting_prep.md) | `meeting.upcoming` | 1 | `prep_and_invite_mail` |
| [`onboarding`](onboarding.md) | `onboarding.completed` | 1 | — |
| [`post_meeting`](post_meeting.md) | `meeting.completed` | 4 | `report_to_participants`, `external_participants`, `attendee_domains`, `data_statement` |
| [`workspace_invite`](workspace_invite.md) | `workspace.invited` | 1 | — |

The rules are answered in [`POLICIES.md`](../POLICIES.md), one directory up.

## Writing one

A flow is a trigger and an ordered list of steps by name; it is submitted as data and is live in about ten seconds, with no rebuild. **Say what this deployment should do, in the governance chat of `_global`** — the agent maps the sentence onto a trigger and steps from the vocabulary above, shows it as the page it will become, and asks once before it goes live.

> **If you are an agent in this chat and the administrator describes something this deployment should do on its own, read `../asks/flow-author.md` and follow it.** Never answer that you have no instruction: read the vocabulary, map what they said, and show them the flow.

A flow written that way gets its own page here, one per version, at `<flow>@<version>.md`. A step list is changed by adding a version and retiring the old one, so both pages stay and the retired one says which version runs now. Where a sentence needs something no step does, the answer is a page under `proposals/` — the step written out for a developer, never executed.
