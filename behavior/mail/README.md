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

## The four templates

| file | who reads it | when |
|---|---|---|
| `prepare.md` | the ORGANISER, and people who are already users | before the meeting |
| `attendee-head.md` | **a stranger** — an attendee who is not a user | after the meeting, above the shared report |
| `minutes-head.md` | somebody who already knows what Vexa is | after the meeting |
| `workspace-invite.md` | **a stranger** — somebody a person here named by address | when they are invited to a group |

**`workspace-invite.md` is the only mail here that nothing about a meeting produced**
(Vexa-ai/vexa#1632). A person pressed **Add a member…**, was asked for an address and a role, read
one sentence back and said yes — so it is sent on a human's own act rather than on a fan-out, and
no switch on this page can swallow it. It goes only to an address this deployment does not already
know: somebody with an account here is handed the link in the chat instead, because mailing a person
who is signed in on the other side of the same screen is a worse product. Like `attendee-head.md`
it may be the first thing a stranger ever reads from us, so it carries the introduction; unlike it,
it is rendered by `mailtext.render`, so `{{visibility}}` travels as a token rather than as literal
text.

**`prepare.md` never goes to a stranger and never claims a workspace was started.** Founder,
2026-09-02, on a pre-meeting fan-out: *"I'm afraid this will not work for a 50 attendee meeting."*
Nothing is built for a person who has not clicked, and a mail before the meeting has nothing yet to
justify itself with. The stranger's first contact is `attendee-head.md`, after a meeting they were
actually in, which is why that one carries the whole introduction and the other two do not.

## One mail, the same for everyone

Founder simplification, 2026-09-02: the post-meeting mail is **the head, the shared report of the
meeting, and one button** — identical for every recipient. No template carries a per-person section
and no template should grow one.

Personalisation happens **after the click**, in the chat, where the agent can read the person's own
workspace and answer for them. That is the trade: a mail that is the same for fifty people is a mail
that cannot be wrong about any of them, and the button is where "what does this leave on MY plate"
gets answered by something that actually knows. It is also why the head has to earn the click on its
own — it is the whole mail besides the report.

## The one sentence that is not in a file

`email_minutes` composes the organiser's body inline and renders no template, so the sentence that
tells the creator sharing is on — *"Everyone else inside your organisation who was on the invite
gets these notes too. To keep one meeting to yourself, put `#noshare` in the invite."* — lives in
the step, not here. It is written down rather than left to be discovered: it moves into
`minutes-head.md` the moment that step renders it.

**`#noshare` is the per-meeting opt-out** (PRD §16.2 item 3). Sharing is creator-controlled and
default ON — that one value is the loop — and the token in the invite is how a creator excludes a
single meeting without an administrator. It is read by `mailbox.NOSHARE`, anywhere in the ICS, and
it is a whole word: `#noshareholders` is a hashtag about shareholders. The deployment-wide switch
beside it is the `attendee_followup` flow param, which is an admin's, not a creator's.

The sentence is CONDITIONAL on the fan-out actually being on. A mail that announces sharing on a
deployment that does not share is the same class of untruth as a mail titled "Minutes" with no
minutes under it.

## Substitutions

**Each template gets only the tokens its own step fills**, and they are not the same set. A token a
step does not fill is left STANDING, not blanked — a visible `{{organizer}}` in a test inbox is a bug
report; a silently empty sentence is not — so putting a token into the wrong file ships braces to a
customer.

`prepare.md`, `minutes-head.md` and `workspace-invite.md` (rendered by `flows_steps/mailtext.render`):

| token | becomes |
|---|---|
| `{{company}}` | the company's name — `_global/README.md`'s first heading, written by the admin at setup |
| `{{service}}` | one sentence of what Vexa does — **fixed product text**, see below |
| `{{title}}` | the meeting's title |
| `{{when}}` | when it is, or was, in the reader's own clock |
| `{{organizer}}` | who had Vexa in the room |

`prepare.md` and `minutes-head.md` also get `{{visibility}}` and `{{workspace}}` — see below.

`workspace-invite.md` gets `{{company}}`, `{{service}}` and `{{visibility}}` from the renderer, plus four its own step fills: `{{inviter}}` (who asked), `{{workspace_name}}` (the group they were invited to), `{{role}}` and `{{role_sentence}}` (what that role IS, derived in `workspace_membership.ROLE_SENTENCES` from `behavior/global/POLICIES.md`'s own line). It gets none of the meeting tokens, because no meeting produced it.

`attendee-head.md` (rendered by `email_attendees` in `flows_defs/production.py`):

| token | becomes |
|---|---|
| `{{company}}` | the company's name, from the same `_global` README |
| `{{organizer}}` | who had Vexa in the room |
| `{{meeting}}` | the meeting's title |
| `{{date}}` | the day it happened, in the organiser's zone |

**`attendee-head.md` carries the visibility sentence as literal text**, not as `{{visibility}}`, for
the same reason it carries the service sentence literally: its renderer fills four tokens and would
mail the braces. If that sentence changes it changes in three files at once — this one,
`mailtext.VISIBILITY_SENTENCE`, and `asks/setup-global.md`, which tells the admin the same thing.

## Who can see what

Founder decision 21, 2026-09-02: **a person's own workspace is not private from the company.** The
attendee head and the minutes head both say so, in his words:

> Vexa runs on this organisation's own servers; what you and your colleagues keep in your workspaces
> is visible to the company's agents; recordings and transcripts stay here.

It is in the first mail a stranger ever gets from us, because that is the only moment at which
telling them is still a choice they can act on. The `_global` setup chat tells the administrator the
same thing and records their own answer in `STRUCTURE.md`.

Note the wording: "your workspaces", the ordinary English word — not the product's NAME for a
person's own space, which is a **desk** (founder, 2026-09-02: a personal desk, and a group desk for
a group). The name lives behind one constant per runtime, `mailtext.WORKSPACE_WORD` and
`clients/terminal/src/minutes/vocabulary.ts`; templates and presets write `{{workspace}}` rather than
the word. Code paths, slugs and API fields keep saying "workspace" on purpose — a naming decision
should not cost a migration.

The word carries the meaning, which is why "private" was the wrong word: **a desk is company
knowledge held by one person**, and the company's agents may read it for a meeting that person is
in. What stays genuinely private is `_system` — chats, sessions, settings — which is not a desk.

After a meeting the shared artefact goes to every attendee's desk as well as by mail, so the mail
may say so; `attendee-head.md` says it as literal text for the same reason it carries the service
sentence literally — its renderer fills four tokens and would mail `{{workspace}}` verbatim.

**`attendee-head.md` carries the service sentence as LITERAL TEXT, not as `{{service}}`** — its
renderer fills four tokens and would mail `{{service}}` verbatim. If that sentence is ever changed it
must be changed in three places at once: this file, `mailtext.SERVICE_SENTENCE`, and the MCP
instructions in `deploy/dogfood/rig/vexa_control_mcp.py`. That is a known duplication, written down
here rather than discovered later: two renderers of one directory grew from two sides of the same
night, and collapsing them into one is worth doing before a third appears.

### Why `{{service}}` is not editable

The company half of the introduction is a per-deployment fact and belongs to the admin. What Vexa
*does* is not: a company that edits it into something Vexa does not do has written a promise the
product will break, to a stranger, in the first sentence they ever read from us. It lives in
`mailtext.SERVICE_SENTENCE`, and the same sentence is in the MCP instructions so the chat and the
mail introduce the product identically — a person who reads one and then the other must not meet two
different products.
