---
kind: flow-index
flows: 10
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
| [`post_meeting`](post_meeting.md) | `meeting.completed` | 4 | `report_to_participants`, `external_participants`, `attendee_domains`, `data_statement` |
| [`workspace_invite`](workspace_invite.md) | `workspace.invited` | 1 | — |

The rules are answered in [`POLICIES.md`](../POLICIES.md), one directory up.
