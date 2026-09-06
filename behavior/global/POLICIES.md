---
kind: policies
profile: default
agent_reads_desk: on
report_to_participants: on
external_participants: on
bot_joins_on_invite: on
bot_joins_mixed_meetings: on
agent_writes_pages: on
transcript_retention_days: forever
recording_retention_days: 0
newcomer_reads_history: off
global_admin_only: on
open_web: on
prep_and_invite_mail: on
attendee_domains:
data_statement:
---

# Policies

The rules this deployment runs under. The front matter above is the answers; everything below says
what each answer changes, what it buys, what it costs, and what a hostile person does with it.

**A rule has one shape: _subject may action object when relation._** The subjects are `user`,
`participant`, `organizer`, `admin`, `agent` (software acting for exactly one user) and `company`.
The objects are `meeting`, `transcript`, `report`, `workspace` (a desk, a group, `_global`,
`_system`), `page` and `mailbox`. The relations are `participant-of`, `organizer-of`,
`member-of(role)`, `owner-of`, `agent-for`, `admin-of`, `derived-from`, `lives-in` and
`bound-to(group, series)`. The actions are `read`, `write`, `deliver`, `join`, `retain` and
`widen` — and widening is always a human act, never a default.

> The organizer and the group owner are the same person by default and not the same primitive:
> **organizer-of is per meeting and set by the calendar; owner-of is standing and can pass.** The
> organizer of a bound series is the group's owner unless ownership was assigned.

## What is not yours to choose

These are the product's, and they hold whatever the front matter says:

- a **participant** reads the transcript and the report of a meeting they were in;
- a **member** reads a group; an **owner or contributor** writes it;
- **`_system` is read by no agent for anybody else** — chats, sessions and settings are the one
  genuinely private tier, and no rule below can widen it;
- **locality is stated, not chosen.** Inference and search run where this deployment runs. That is a
  fact about the install, not a switch on this page.

## How the rules compose

- **Conjunction, with deny winning.** An action happens when every rule that touches it allows it.
  One `off` is enough to stop it, and no rule below can re-permit what another refuses.
- **Externals filter delivery.** `external_participants` and `attendee_domains` narrow who receives
  something; they never widen who may read it. Neither can put a report in front of somebody who was
  not in the room.
- **Writes are bounded by containment, whatever the writing rule says.** An agent writes inside the
  workspaces the turn mounted, and nowhere else. `agent_writes_pages: on` chooses whether it writes
  at all — never where.
- **The sentence attendees read is DERIVED from this set, never written.** Change a rule and the
  disclosure in the mail changes with it. There is no field for it here on purpose: a disclosure
  somebody can edit away from what the software does is worse than none.

## Profiles

`profile:` applies a preset; any key written explicitly below it wins over the preset.

| profile | what it changes |
|---|---|
| `default` | everything at the defaults in the table below |
| `bank` | externals off · mixed meetings off · transcript-only retention · open web off · the loop levers (report to participants, prep and invite mail) left on |
| `studio` | the defaults, plus recordings retained |

## The rules

<a id="agent_reads_desk"></a>
### `agent_reads_desk` — an agent may read its user's desk when its user is a participant

**Default `on`.** *(founder decision 21, 2026-09-02.)*

**The sentence it changes.** With it on, attendees are told: *what you and your colleagues keep in
your workspaces is visible to the company's agents*. With it off, they are told the opposite, and
the mail says so in the same breath.

**The effect.** In a post-meeting room, the turn mounts the desks of the people who were in the
meeting, read-only, so the report can say what this meeting means for each of them. Off, it mounts
none of them and writes from the transcript alone.

**Adoption.** This is what makes a report worth opening rather than a set of minutes: the difference
between *"we agreed X"* and *"we agreed X, and you own the migration you wrote up last week"*.
**Security.** It is the widest read in the product: one meeting can pull a dozen people's working
notes into one turn. It never reaches `_system`. **Adversarial.** Somebody who gets themselves into
a meeting gets an agent reading the other participants' desks for that meeting — so participation is
resolved from the directory, not from a display name, and the bot announces itself in the room.

**The price of turning it off.** Reports stop being personal and the product becomes a transcriber.

<a id="report_to_participants"></a>
### `report_to_participants` — the report is delivered to every participant

**Default `on`.**

**The sentence it changes.** The organiser is told *everyone else who was on the invite gets these
notes too*, and how to keep one meeting to themselves.

**The effect.** After a meeting, everybody in the room receives the same report — byte-identical,
one artefact, no per-person section. Off, only the organiser is written to.

**Adoption.** **This is the measured lever.** One meeting reaches everybody in the room, and the
people who never asked for Vexa meet it holding something useful about a meeting they were actually
in. Turning it off does not slow the loop down, it removes it. **Security.** Everything the report
says goes to everybody who was there — which is the same set that could already read the transcript,
so it widens delivery, not readership. **Adversarial.** Somebody in the room who should not have
been is a room-admission problem, not a delivery one; the fence is who gets in.

**Per-meeting opt-out, without an administrator:** `#noshare` anywhere in the invite.

**The price of turning it off.** The invite loop stops. Growth becomes something somebody has to do
by hand.

<a id="external_participants"></a>
### `external_participants` — an external participant is delivered the report

**Default `on`.**

**An external is a participant with no desk here** — someone who was in the room and has never
signed in. It is not another word for another domain: the line between inside and outside is
[`attendee_domains`](#attendee_domains), and *outside the domain, never* holds whatever this rule
says.

**The sentence it changes.** The first mail a stranger ever gets from us exists because of this
rule; it introduces the company, the product, the meeting, and who can see what.

**The effect.** A participant who is not yet a user is mailed the report like anyone else. Off, only
people who already have an account are written to, and everyone else meets Vexa only if they come
looking.

**Adoption.** The loop's reach: most people in a large meeting have never heard of us, and this is
the one moment they are holding something we made about a meeting they were actually in.
**Security.** It writes to an address that has never authenticated — the report goes to whoever the
calendar said was there, inside the domain, whether or not they have ever proved they are that
person. **Adversarial.** An invite is not an assertion of identity, so an attacker who gets an
address onto an invite receives the report. Three fences: the domain line, the fact that the report
is what was said in a room that person was admitted to, and a share link that grants a new reader
nothing when it is forwarded.

**The price of turning it off.** The invite loop only reaches people who already arrived, which is
the opposite of a loop.

<a id="bot_joins_on_invite"></a>
### `bot_joins_on_invite` — the bot joins a meeting when the mailbox is invited

**Default `on`.**

**The sentence it changes.** *Add the mailbox to any meeting; I sit in it.* That sentence is the
whole product surface for a person who has never opened the terminal.

**The effect.** An invite to this deployment's mailbox is the join request. Off, an invite is
recorded and nothing joins; a meeting is attended only when a person asks for it in the product.

**Adoption.** Nothing else is this cheap to try: forwarding an invite costs one action and no
account. **Security.** The mailbox is a door on the public internet; what comes through it is
untrusted, rate-limited per sender and deployment-wide, and bounded by the inbound domain
allow-list. **Adversarial.** Somebody invites the mailbox to a call they are in but should not
record — so the bot announces itself in the room and appears as a participant, which is what lets
anybody present object before a word is transcribed.

**The price of turning it off.** The product loses its front door.

<a id="bot_joins_mixed_meetings"></a>
### `bot_joins_mixed_meetings` — the bot joins a meeting with external participants

**Default `on`.**

**The effect.** A meeting whose invite carries addresses outside the organisation is attended like
any other. Off, the bot stays out of it, and the meeting produces nothing.

**Adoption.** Customer calls are the meetings people most want written up. **Security.** It is the
rule that decides whether a stranger is ever in a room our software is listening to. **Adversarial.**
A hostile external participant is a person speaking into a transcript that other agents later read —
so the transcript is untrusted text everywhere downstream and never an instruction.

**The price of turning it off.** Internal meetings only. For a bank, that is the point.

<a id="agent_writes_pages"></a>
### `agent_writes_pages` — an agent may write pages into a workspace from a meeting when every member was a participant

**Default `on`.**

**The effect.** After a meeting, what was learned is written into the group's pages — but only when
every member of that group was in the room. A group with one absent member gets nothing written.

**Adoption.** This is the difference between a transcript service and knowledge that accumulates:
next month's meeting starts from what last month's produced. **Security.** The containment rule is
the fence and it is not this switch: a turn writes inside what it mounted. This rule decides whether
it writes at all. **Adversarial.** Prompt injection through a transcript or a fetched page tries to
make an agent write somewhere it should not — containment is what makes that attempt land inside the
same workspace instead of somewhere else, and every write is a commit with an author.

**The price of turning it off.** Meetings produce mail and nothing durable.

<a id="transcript_retention_days"></a>
### `transcript_retention_days` / `recording_retention_days` — how long the words and the audio are kept

**Defaults: transcripts `forever`, recordings `0`.**

**The sentence it changes.** The retention clause of the disclosure every attendee reads is composed
from these two numbers.

**The effect.** `forever` keeps it for as long as the deployment holds it; a number is a count of
days; `0` means it is not kept at all. Nothing here moves data off this deployment either way —
locality is not a retention setting.

**Adoption.** A searchable history is what makes the product worth keeping after the novelty. A
recording is what a person asks for exactly once, when the transcript missed something.
**Security.** Audio is the most sensitive artefact the product ever holds and the hardest to
anonymise, which is why `0` is the default and keeping it is the deliberate act. **Adversarial.**
Retention is what an attacker who gets in later inherits: everything still held is in scope.

**The price.** Short retention costs the history; long recording retention costs the exposure.

<a id="newcomer_reads_history"></a>
### `newcomer_reads_history` — a newcomer to a series reads its earlier reports

**Default `off`.**

**The effect.** Somebody added to a recurring meeting in October can, with this on, read the reports
from the meetings before they joined. Off, they see the series from their first meeting forward.

**Adoption.** Context on arrival is the single most useful thing a new joiner can be handed.
**Security.** It hands somebody the substance of meetings they were not in — the one place where
*participant-of* stops being the boundary, which is why this is the only rule that ships `off`.
**Adversarial.** Getting added to a standing invite is much easier than getting into a specific
meeting; with this on, one addition opens the archive.

**The price of turning it on.** Membership of a series becomes retroactive access. Do it because
somebody decided to, not because it was convenient.

<a id="global_admin_only"></a>
### `global_admin_only` — only the admin writes `_global` (editors may be added)

**Default `on`.**

**The effect.** One admin's session mounts `_global` read-write; every other agent in the deployment
mounts it read-only. Named editors may be added; the set is never "everyone".

**Adoption.** One person can change how every agent in the company behaves, with no deploy.
**Security.** That same sentence is the risk: `_global` is loaded into every turn, so a line written
here is executed by every agent for everybody. **Adversarial.** An editor who writes an instruction
into `_global` has written it into every conversation in the company — so the set of writers is
named, and **every write is a commit with an author**, reviewable and revertable.

**The price of widening it.** The blast radius of one edit is the whole deployment.

<a id="open_web"></a>
### `open_web` — an agent may fetch from the open web

**Default `on`.**

**The effect.** An agent may search and fetch public pages while it works — looking up a company
before a meeting, checking a fact in a report. Off, it works only from what this deployment holds.

**Adoption.** It is most of the difference between a note-taker and an assistant, and it shows up
first at setup, when the product can say what the company does without being told.
**Security.** It is an outbound path from inside the network. **Adversarial.** Two shapes: a fetched
page is untrusted text and never an instruction, and a URL an agent is talked into fetching is an
SSRF attempt — private and link-local ranges are refused at the fetcher, not at the prompt.

**The price of turning it off.** The agent knows only what is already here. For an air-gapped
install that is not a cost, it is the requirement.

<a id="prep_and_invite_mail"></a>
### `prep_and_invite_mail` — prep mail and the invite line to organizers

**Default `on`.**

**The sentence it changes.** The prep note before a meeting, and the line that tells an organiser how
to put Vexa in a meeting of their own.

**The effect.** Before a meeting, the organiser and people who already have a desk get a short prep
note. Off, nothing is sent before a meeting.

**Adoption.** **The second measured lever**, and the cheapest one: the prep line is where a first
invite turns into a second. **Security.** It goes only to the organiser and to existing users, never
to a stranger — a fifty-person meeting must not produce fifty mails to people who have never heard
of us. **Adversarial.** A mail before a meeting is a mail about a meeting that has not happened, so
it carries no content and claims nothing was built for anybody.

**The price of turning it off.** Vexa becomes something that only ever speaks after the fact.

<a id="attendee_domains"></a>
### `attendee_domains` — which domains count as inside

**Default: empty, which means the organiser's own domain.** Comma-separated, with or without a
leading `@`.

**Empty is not "everyone".** It means the organiser's domain, exactly as the inbound allow-list
unset means the mailbox's own. An allow-list whose empty value is "everybody" is a footgun with a
default.

**The effect.** It is the line between inside and outside for delivery. Together with
`external_participants` it decides who receives a report: inside always, outside only when externals
are on.

<a id="data_statement"></a>
### `data_statement` — this deployment's own sentence about where the words live

**Default: empty, which means the derived sentence.**

A studio running this on its own hardware may want to say so in its own words. Written here, it
replaces the locality clause of the disclosure. Left empty, the disclosure is composed from the
rules on this page, which is the shape that cannot drift away from what the software actually does.

## Where each rule is read today

Written down rather than left to be discovered. A rule that nothing reads is a control that silently
does nothing, which is worse than one that is not there — so this table says which are enforced by
code today and which are, for now, a written commitment the setup conversation records and the
product does not yet check.

| rule | read by |
|---|---|
| `agent_reads_desk` | the derived disclosure in every post-meeting mail |
| `report_to_participants` | the attendee fan-out (`email_attendees`) |
| `external_participants` | whether the fan-out writes to a participant with no account (`email_attendees`) |
| `attendee_domains` | the line between inside and outside, for the same fan-out |
| `data_statement` | the provenance line above every minutes mail |
| `prep_and_invite_mail` | the prep note before a meeting (`emit_prep`) |
| `transcript_retention_days`, `recording_retention_days` | the derived disclosure |
| `bot_joins_on_invite`, `bot_joins_mixed_meetings` | **declared, not yet enforced** — admission would have to read this file before dispatching a bot |
| `agent_writes_pages` | **declared, not yet enforced** — containment already bounds where a turn writes; this switch does not yet gate whether it writes |
| `newcomer_reads_history` | **declared, not yet enforced** — and it is `off`, which is the safe direction to be unenforced in |
| `global_admin_only` | **structural** — the `_global` mount is read-only except in the admin's own session; this line records it rather than deciding it |
| `open_web` | **declared, not yet enforced** — the agent's web tools are chosen at dispatch, not read from here |

Every line marked *not yet enforced* is a gap in the software, not in the policy. It belongs in
[`MISSING.md`](MISSING.md) if this deployment is relying on it.
