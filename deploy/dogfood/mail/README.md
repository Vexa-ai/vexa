# mail/ — the words this deployment sends, as files

**PLACEHOLDER WORDING. Every body in this directory is the founder's to rewrite, and the rewrite is
a file edit.** That is the whole point of the directory: a first impression has to be fixable at the
speed somebody notices it is wrong, and a sentence living in a Python step is fixable only at the
speed of a review, a rebuild and a deploy.

These files are the SOURCE. The live copies a send actually reads are at
`/workspaces/_global/mail/<name>.md` on the stack — same content, edited in both, or the source
lies. Nothing is rebuilt when they change; the next mail picks up the new text. If `_global` holds
no override, the step falls back to the identical baked default in
[`flows_steps/mailtext.py`](../../../core/flows/src/flows_steps/mailtext.py) — so a fresh deployment
mails correctly before anyone has edited anything.

## Format

    subject: <the subject line, substitutions allowed>
    ---
    <the body, substitutions allowed>

Plain text. One link at most, and the step appends it — a template never writes a URL, because a
link that a template could write is a link anyone who can edit a file can point anywhere.

## The three templates

| file | who reads it | when |
|---|---|---|
| `prepare.md` | the ORGANISER, and people who are already users | before the meeting |
| `attendee-head.md` | **a stranger** — an attendee who is not a user | after the meeting, above the agent's own per-person section |
| `minutes-head.md` | somebody who already knows what Vexa is | after the meeting |

**`prepare.md` never goes to a stranger and never claims a workspace was started.** Founder,
2026-09-02, on a pre-meeting fan-out: *"I'm afraid this will not work for a 50 attendee meeting."*
Nothing is built for a person who has not clicked, and a mail before the meeting has nothing yet to
justify itself with. The stranger's first contact is `attendee-head.md`, after a meeting they were
actually in, which is why that one carries the whole introduction and the other two do not.

## Substitutions

| token | becomes | filled by |
|---|---|---|
| `{{company}}` | the company's name | `_global/README.md`'s first heading — written by the admin at instance setup |
| `{{service}}` | one sentence of what Vexa does | **fixed product text**, not editable here — see below |
| `{{title}}` | the meeting's title | the step |
| `{{when}}` | when it is, or was, in the reader's own clock | the step |
| `{{organizer}}` | who had Vexa in the room | the step |
| `{{attendees}}` | how many other people were in it | the step |

An unknown `{{token}}` is left STANDING, not blanked. A visible `{{organizer}}` in a test inbox is a
bug report; a silently empty sentence is not.

### Why `{{service}}` is not editable

The company half of the introduction is a per-deployment fact and belongs to the admin. What Vexa
*does* is not: a company that edits it into something Vexa does not do has written a promise the
product will break, to a stranger, in the first sentence they ever read from us. It lives in
`mailtext.SERVICE_SENTENCE`, and the same sentence is in the MCP instructions so the chat and the
mail introduce the product identically — a person who reads one and then the other must not meet two
different products.
