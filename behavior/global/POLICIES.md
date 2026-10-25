---
kind: policies
profile: default
agent_reads_desk: on
report_to_participants: on
external_participants: on
bot_joins_on_invite: on
organizer_confirms_join: off
bot_joins_mixed_meetings: on
agent_writes_pages: on
transcript_retention_days: forever
recording_retention_days: 0
newcomer_reads_history: off
global_admin_only: on
open_web: on
prep_and_invite_mail: on
minutes_mail: on
attendee_domains:
mail_domains:
data_statement:
user_flow_steps:
routine_unattended_actions: bot.request, bot.stop, flow.retire_self
routine_pauses_when_shared: on
agent_messages_own: on
agent_messages_shared: off
agent_messages_from_routines: off
session_agent_speaks: mentioned
connection_actions: all
connection_actions_unattended: none
calendar_notify_attendees: off
calendar_attendee_domains: any
calendar_ids: primary
draft_recipient_domains: any
draft_attachments: off
draft_attachment_max_kb: 5120
post_meeting_auto_accept: off
folder_import_max_mb: 200
folder_import_max_files: 10000
folder_import_file_max_mb: 25
chat_turn_max_tool_calls: 40
shell_timeout_sec: 120
features:
mail.sign-in: on
mail.workspace-invite: on
mail.workspace-joined: on
mail.meeting-share: on
mail.chat-share: on
mail.call-invite: on
mail.bot-scheduled: off
mail.bot-joined: off
mail.bot-failed: off
mail.prepare: off
mail.minutes: off
mail.attendee-report: off
mail.recording: off
mail.report-daily: off
mail.report-weekly: off
mail.digest: off
mail.reply: on
mail.rsvp: on
mail.quarantine-reply: off
mail_caps:
home_history_retention_days: forever
home_cards: off
home_people: on
external_images: on
presence_visibility: workspace_members
desk_placements_max: 50
card_proposals_pending_max: 5
card_proposals_per_agent_per_day: 20
card_proposal_ttl_days: 7
lifecycles.card: To do > Doing > Done | Dropped
card_stalled_days: 14
budget_tokens_per_user_day: 2000000
budget_tokens_routines_share: 50
routine_runs_per_day: 48
routine_min_interval_minutes: 10
home_compose_per_day: 14
home_expand_per_day: 40
event_agent_runs_per_day: 30
invite_signin_days: 7
event_agent_max_trust: draft
model_chat_default:
models_allowed: all
model_routines: cheapest
model_home: cheapest
model_event_agents: cheapest
models_for:
model_rules:
model_levels:
model_intent_router:
subscription_token_proxy: deny
model_scope_unpinned: runner_family
appearance_accent: clay
appearance_font_ui: geist
appearance_font_reading: ui
llm_budget_org_daily_usd: 25
llm_budget_org_monthly_usd: 250
llm_budget_user_daily_usd: 5
llm_budget_user_monthly_usd: 50
llm_price_input_usd_per_mtok:
llm_price_output_usd_per_mtok:
llm_budget_notify:
transcript_signals: off
model_signals:
signals_wakes_per_meeting: 12
signals_cost_per_meeting_usd: 0.50
signals_default_brief:
credit_markup: 1.3
credits_5h:
credits_week:
---

# Policies

The rules this deployment runs under. The front matter above is the answers; everything below says
what each answer changes, what it buys, what it costs, and what a hostile person does with it.

**A rule has one shape: _subject may action object when relation._** The subjects are `user`,
`participant`, `organizer`, `admin`, `agent` (software acting for exactly one user) and `company`.
The objects are `meeting`, `transcript`, `report`, `workspace` (a personal workspace, a group, `_global`,
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
- **`_system` is read by no agent for anybody else** — identity and settings are the one genuinely
  private tier, and no rule below can widen it. Chats are in no workspace: they are kept in the
  session store, and only the people in a chat read it;
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
### `agent_reads_desk` — an agent may read its user's personal workspace when its user is a participant

**Default `on`.** *(founder decision 21, 2026-09-02.)*

**Declared, not yet enforced.** No access path reads this rule. A post-meeting room mounts no
other person's personal workspace whatever the answer, and a personal workspace is readable by any
signed-in member of this instance, and so by any agent working for one, whatever the answer. Off
is therefore a written commitment, not a switch: say *intended, not yet enforced* when you
recommend it.

**The sentence.** Attendees are told *what you and your colleagues keep in your workspaces is
visible to the company's agents*, because that is what the software does. The answer here does not
change it: the mail does not promise a narrower read than the product enforces.

**The intended effect.** In a post-meeting room, the turn would read the personal workspaces of the
people who were in the meeting, so the report can say what this meeting means for each of them.
Off, it would write from the transcript alone.

**Adoption.** This is what makes a report worth opening rather than a set of minutes: the difference
between *"we agreed X"* and *"we agreed X, and you own the migration you wrote up last week"*.
**Security.** It is the widest read in the product: one meeting can pull a dozen people's working
notes into one turn. It never reaches `_system`. **Adversarial.** Somebody who gets themselves into
a meeting gets an agent reading the other participants' personal workspaces for that meeting — so participation is
resolved from the directory, not from a display name, and the bot announces itself in the room.

**The price of turning it off.** Reports stop being personal and the product becomes a transcriber.

<a id="session_agent_speaks"></a>
### `session_agent_speaks` — when the agent answers in a chat with other people in it

**Default `mentioned`.** *(ADR-0048 decision 5 — a default the founder may strike.)*

**The sentence it changes.** With `mentioned`, people in a shared chat are told *the agent answers
when you write @vexa or ask it directly*; with `always`, *the agent answers every message*.

**The effect.** In a chat with one person the agent answers everything either way. With two or more,
`mentioned` keeps every other message as a message the others read and the agent hears on its next
turn; `always` makes every message a turn. A workspace's own `POLICIES.md` may set it for chats
attached to that workspace.

**Adoption.** People can talk to each other in the chat without the agent interrupting.
**Security.** Neither answer widens what the agent may read: a shared chat's turn uses only what every
participant can read, whatever this says. **Adversarial.** `always` lets any participant spend the
owner's turns by talking; quotas still apply.

**The price of the other answer.** `always` turns a conversation between people into a transcript of
the agent replying to everything.

<a id="post_meeting_auto_accept"></a>
### `post_meeting_auto_accept` — a change proposed after a call lands without its owner's decision

**Default `off`.** *(ADR-0048 decision 7.)*

**The sentence it changes.** With `off`, the owner of a call is told *these changes wait for you*;
with `on`, *these changes were made for you*.

**The effect.** After a call held in a chat, the post-meeting run proposes the report as a page on
the owner's desk and each commitment it names. With `off`, nothing lands until the owner accepts,
edits or rejects each one. With `on`, each lands at once, written with the owner's own rights and
committed in their name. A workspace's own `POLICIES.md` may set it for pages proposed into that
workspace.

**Adoption.** `on` removes a step for people who never review. **Security.** Neither answer widens
where a change may land: the owner's own write rights apply either way. **Adversarial.** With `on`,
whatever the meeting's report says reaches a desk unread.

**The price of the other answer.** `on` puts unreviewed words into workspaces other people read.

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

**An external is a participant with no personal workspace here** — someone who was in the room and has never
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

<a id="organizer_confirms_join"></a>
### `organizer_confirms_join` — the organizer confirms each join before the bot is admitted

**Default `off`.** **Declared, not yet enforced** — see the table at the foot of this page.

**The effect.** Off, an invite is the whole decision: whoever put the mailbox on the invite has
asked for the meeting to be attended. On, the meeting's organizer is asked to confirm before the bot
is admitted, and an invite from anybody else is a request rather than an instruction.

**Adoption.** Off is what makes [`bot_joins_on_invite`](#bot_joins_on_invite) cost one action and no
account; a confirmation is a second step in front of every first try, and the first try is the one
that decides whether there is a second. **Security.** It is the only rule that puts the meeting's
own owner between an invite and a recording — the organizer knows who should be in the room, and the
mailbox does not. **Adversarial.** Somebody who can get an address onto an invite can, with this
off, have a call recorded that they were never meant to record; on, that attempt reaches the person
best placed to refuse it. The bot announcing itself in the room is the fence that holds either way,
and it is a fence that acts after the first words rather than before them.

**The price of turning it on.** Every join waits on a person, and the joins that matter most are the
ones nobody is watching the calendar for.

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

**Declared, not yet enforced.** Nothing deletes a transcript or a recording on a schedule, and
whether a bot records is the deployment's `RECORDING_ENABLED`, not this rule. A number of days, or
a recording `0`, is a written commitment: say *intended, not yet enforced* when you recommend it.

**The sentence.** The answers here do not change what attendees are told: the mail names no number
of days and promises no absent recording until the product deletes on schedule, and it does not say
where recordings and transcripts are kept. Saying that is [`data_statement`](#data_statement)'s job.

**The intended effect.** `forever` keeps it for as long as the deployment holds it; a number is a
count of days; `0` means it is not kept at all. Nothing here moves data off this deployment either
way — locality is not a retention setting.

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
*participant-of* stops being the boundary, which is why it ships `off` — one of the two rules
that do. The other is [`organizer_confirms_join`](#organizer_confirms_join), which is off
because it describes a gate this deployment does not have yet.
**Adversarial.** Getting added to a standing invite is much easier than getting into a specific
meeting; with this on, one addition opens the archive.

**The price of turning it on.** Membership of a series becomes retroactive access. Do it because
somebody decided to, not because it was convenient.

<a id="global_admin_only"></a>
### `global_admin_only` — only the admin account writes `_global`

**Default `on`.**

**The effect.** The deployment's **admin account** — one dedicated account, never a person's everyday
one — mounts `_global` read-write, and has nothing else: no desk, no shared workspaces, no meetings.
Every other account in the deployment, the instance admin's included, mounts it read-only. The
operator names the admin account (`VEXA_ADMIN_ACCOUNT` on admin-api); with none named, nobody writes
here. The set is never "everyone".

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

**The effect.** Before a meeting, the organiser and people who already have a personal workspace get a short prep
note. Off, nothing is sent before a meeting.

**Adoption.** **The second measured lever**, and the cheapest one: the prep line is where a first
invite turns into a second. **Security.** It goes only to the organiser and to existing users, never
to a stranger — a fifty-person meeting must not produce fifty mails to people who have never heard
of us. **Adversarial.** A mail before a meeting is a mail about a meeting that has not happened, so
it carries no content and claims nothing was built for anybody.

**The price of turning it off.** Vexa becomes something that only ever speaks after the fact.

<a id="minutes_mail"></a>
### `minutes_mail` — the minutes mail after a meeting

**Default `on`.**

**The effect.** After a meeting, the organiser is mailed the minutes, and the members of a group the
meeting is bound to get the same mail. Off, nobody is mailed the minutes; the report is still
written and still readable in the product. Each person's own `mail_minutes` setting sits under this
rule: deny wins either way.

**Adoption.** The minutes mail is how most people first read what Vexa made. **Security.** It sends
what was said in a meeting to the people who own it, nobody else. **Adversarial.** Nothing beyond
what the meeting's own organiser could already read.

**The price of turning it off.** The product stops speaking after a meeting unless someone opens it.
The attendee follow-up is a separate rule ([`report_to_participants`](#report_to_participants)).

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

<a id="mail_domains"></a>
### `mail_domains` — which domains the mailbox takes invitations and mail from

**Default: empty, which means `VEXA_FLOWS_MAIL_DOMAINS` if the operator set it, else the mailbox's
own domain.** Comma-separated, with or without a leading `@`. Each entry must be a domain: `*`, a
bare top-level domain, an address or a URL is ignored and named as a problem, never read as
"everyone".

**The effect.** The inbound allow-list. Mail and invitations from a person who already has an
account are always handled. From anybody else, they are handled only when the sender (for an
invitation, its organiser) is inside one of these domains; everything else is recorded and
quarantined: no account, no agent turn, no reply. *"Only accept invites from bank.com"* is
`mail_domains: bank.com`.

**Adoption.** A colleague at a sister company can invite the mailbox without an account first.
**Security.** This is the door on the public internet; every domain added is a population that can
make this deployment create accounts and spend model turns, rate-limited. **Adversarial.** A sender
address is not proof of identity, so a domain here is trusted as far as its mail is.

**What stays deployment configuration.** The mailbox address, its password or app registration, the
SMTP relay and the rate limits are the operator's (`VEXA_MAIL_*`, `VEXA_GRAPH_*`,
`VEXA_FLOWS_MAIL_RATE_*`); they are never written here and never shown to an agent.

<a id="data_statement"></a>
### `data_statement` — this deployment's own sentence about where the words live

**Default: empty, which means the derived sentence.**

A studio running this on its own hardware may want to say so in its own words. Written here, it
replaces the locality clause of the disclosure, so attendees read it followed by *; what you and
your colleagues keep in your workspaces is visible to the company's agents.* Left empty, the
disclosure is composed from the rules on this page, which is the shape that cannot drift away from
what the software actually does.

The derived sentence does not say where recordings and transcripts are kept, because a supported
install may not keep them on its own servers: hosted transcription receives the audio, helm with an
external object store keeps the recordings there, and a hosted model reads the transcript. An
operator who can promise more writes it here — *Recordings and transcripts stay in our Frankfurt
rack* — and owns the promise.

<a id="user_flow_steps"></a>
### `user_flow_steps` — the steps a person's own flow may use

**Default: empty, which means the steps the image marks for people — `agent_run` alone.**
Comma-separated step names from the vocabulary (`GET /flows` lists it).

**The effect.** Anyone with an account may own flows of their own — a routine is one: a schedule
and an agent turn. This list is the set of steps such a flow may name. A submission naming any other
step is refused with `step_not_allowed`, naming the step. The instance's own flows are not limited
by it.

**Security.** Every step added here is something any person can make the deployment do on a clock,
for themselves. `agent_run` runs their own agent with nobody watching, in the `autonomous` regime,
so the tools that spend credentials, send mail or delete are refused unless this file allows them.
A step that mails other people, or acts on a meeting, is a different grant. **Adversarial.** The
loop guard and the per-person quotas (active flows, the shortest gap between fires, runs per day)
hold whatever this list says.

**The price of narrowing it to nothing.** People keep no routines.

<a id="routine_unattended_actions"></a>
### `routine_unattended_actions` — what a routine may be granted to do on its own

**Default: `bot.request, bot.stop, flow.retire_self`.** Comma-separated, from exactly these three, or
`none`.

**The effect.** A routine runs with nobody in the loop, so everything that needs a person — putting
a bot into a meeting among them — is refused to it. A person who makes a routine in their chat to do
one such thing ("send a bot to this meeting at 12:13") grants it that act by name when they make it,
and is told so in words ("This routine may: send bots to meetings"). This list is the most any
routine may be granted: `bot.request` puts a bot into a meeting, `bot.stop` takes it out,
`flow.retire_self` lets the routine retire or pause itself. A grant outside it is refused when the
routine is saved (`grant_not_allowed`) and dropped at every run, so narrowing it takes effect on the
next run of every routine. Mail, calendar and connection acts are not grantable here; those are
`connection_actions_unattended`.

**Security.** A grant is narrow by construction: it is carried only on that routine's own unwatched
run, opens only the verb it names, can be revoked from the routine (`revoke_grants`), and each use is
logged (`routine_grant_used`). **Adversarial.** A person can only grant their own routine, and only
from a chat they are in — a routine running unwatched cannot write or widen any flow. The worst a
hostile person does with `bot.request` is send their own bot to a meeting they have the link to,
which they can do by hand.

**The price of `none`.** "Send a bot to this meeting at 12:13" needs the person present at 12:13.

<a id="routine_pauses_when_shared"></a>
### `routine_pauses_when_shared` — a routine whose chat became shared

**Default: `on`.**

**The effect.** A chat with two or more people in it reads only what every one of them can read, so
a routine running in a chat its owner shared is refused the owner's mail, calendar, meetings and
connections (`shared_conversation`). With this `on`, the first run refused that way ends refused, the
routine pauses, and its chat says so in one line: who the chat is shared with, what could not be
read, and to unshare the chat or move the routine to a private chat, then resume. The routine's strip
shows it paused, with that reason. A run that needs nothing private keeps running as before. With it
`off`, the run fails with its reason and the routine stays on.

**Security.** The routine is never run with its owner's private reach in a chat other people read,
and nothing it writes there is filtered after the fact: no filter over free text is reliable. Pausing
only stops runs; it never widens what a run may read. **Adversarial.** A participant cannot resume
another person's routine: resuming is its owner's act.

**The price of `off`.** A routine that needs a private source fails on every run until its owner
notices.


<a id="agent_messages"></a>
### `agent_messages_own`, `agent_messages_shared`, `agent_messages_from_routines` — who an agent may write to

**Defaults: on, off, off.** An agent's address is its chat thread, and the `message_agent` tool
queues a turn in another thread, marked as from the sending agent, never as the person's words.

**The effect.** `agent_messages_own` lets an agent write to another of its own person's threads — a
chat handing work to a routine, a routine reporting into a chat. `agent_messages_shared`, when on,
lets it write to another person only in a conversation (`s-…`) both of them take part in, where the
sender takes part with write, inside a workspace both belong to where the sender is not a reader.
Another person's own threads — their `main`, their routines' threads, a conversation the sender is
not in or only reads — are never an address, whatever this key says. `agent_messages_from_routines`
lets a routine — an agent running on a clock with nobody watching — send at all; a worker running
with nobody in the loop counts as a routine unless it is answering a message delivered into its
thread.

**Security.** A message is a turn somebody else's agent runs, so it is an instruction crossing from
one person to another. It crosses only into a conversation the sender already takes part in and
writes to, it runs there with nobody in the loop, and it carries who sent it on its face. Every way
a message to another person can fail answers the same refusal, so a refusal says nothing about
which of that person's threads exist. **Adversarial.** Two agents answering each other forever stop
at the loop guard's hop limit; one agent flooding another stops at the per-person daily message
count. Every refusal is typed and names the key.

**The price of turning them off.** Agents hand nothing to each other; a person relays it by hand.

## Connected mail and calendar

These keys decide what an agent may do with a person's own connected Gmail and Google Calendar.
They are **enforced by the software**, twice: agent-api refuses before it asks, and the credential
broker refuses again with the same answer. A refusal names the key that refused it.

**A person can only narrow them.** Each person may keep a stricter setting for themselves
(`connection_policy_set`); a setting that would allow more than this page is refused, naming the
key. When this page narrows later, a person's older, wider setting is clipped at once.

**Loud, not soft.** The meeting rules above fall back to their default when a value cannot be read.
These do the opposite: a value that cannot be read, or a key starting `connection_`, `calendar_` or
`draft_` that is not one of these (a typo, usually), refuses every connection action until it is
fixed. Software writing to somebody's calendar on a guess is the failure this page exists to stop.

<a id="connection_actions"></a>
### `connection_actions` — what an agent may do while its person is present

**Default `all`.** `all`, `none`, or a comma-separated list of: `gmail.search`, `gmail.read`,
`gmail.thread`, `calendar.events` (the reads), `gmail.draft`, `gmail.draft.update`,
`calendar.event.create`, `calendar.event.update`, `calendar.event.cancel` (the writes).

**The effect.** An action not listed is refused even when the person asks for it in the chat.
**Security.** Nothing here sends mail: a draft is never sent, and the person sends it from Gmail.
Calendar writes change the person's own calendar and need the separate *Enable calendar changes*
permission in Connections. **The price of narrowing it.** The agent can say what it would do, and
the person does it by hand.

<a id="connection_actions_unattended"></a>
### `connection_actions_unattended` — which reads an agent may take in a background routine

**Default `none`.** A comma-separated list of the reads (`gmail.search`, `gmail.read`,
`gmail.thread`, `calendar.events`), or `none`; an action must be in both keys. A write
(`gmail.draft`, `gmail.draft.update`, `calendar.event.*`) or `all` here makes the file unreadable,
and every connection action is refused until it is fixed.

**The effect.** A routine runs with nobody in the chat. With `none`, it reads no mailbox and no
calendar; it records what it wanted to do and stops. It never drafts or changes a calendar, whatever
this key says: a write always needs the person in the loop. **Adversarial.** A routine reads mail and
pages that other people wrote; a write there would let text nobody in the loop has read lead to a
draft or a calendar change. So writes are not a setting, and reads are turned on per action,
deliberately.

<a id="calendar_notify_attendees"></a>
### `calendar_notify_attendees` — whether Google may email attendees about an agent's change

**Default `off`.** Off, every create, update and cancel is made with Google's *send no updates*,
and an agent asking for notification is refused. On, the agent may ask Google to email the attendees,
per action, with `notify: true` — only when the person asked for the event and its attendees in the
chat, never in a routine (calendar writes always need the person present). Every notified change is
audited as its own act. **Why off.** An invitation is mail to other people, sent in the person's
name; this product does not send mail for a person without them sending it. A company whose people
want their agent to send invitations turns it on here.

<a id="calendar_attendee_domains"></a>
### `calendar_attendee_domains` — which domains an agent may add as attendees

**Default `any`.** `any`, or a comma-separated list of domains. **Why `any`, unlike
`attendee_domains`.** That key decides who *receives a report*; this one decides whom the person, who
is present and asked, may put on their own event — customer calls are most of a calendar. With
notification off, adding an attendee mails nobody. A bank narrows it to its own domains.

<a id="calendar_ids"></a>
### `calendar_ids` — which calendars an agent may change

**Default `primary`.** `any`, or a comma-separated list of Google calendar ids (`primary` is the
person's own). Reading is unaffected.

<a id="draft_recipient_domains"></a>
### `draft_recipient_domains` — which domains a draft may address

**Default `any`.** `any`, or a comma-separated list of domains, applied to every To, Cc and Bcc
address. A draft is never sent, so this governs what the agent prepares, not what leaves.

<a id="draft_attachments"></a>
### `draft_attachments` / `draft_attachment_max_kb` — workspace files on a draft

**Defaults `off` and `5120`.** On, a draft may carry files from a workspace the person can read:
PDF, PNG, JPEG, plain text, CSV or Markdown, at most `draft_attachment_max_kb` kilobytes in all
(never above 20480). **Adversarial.** An attachment is how a file leaves the workspace once the
person presses send; the person sees it in the draft before that.

## Folders dropped into the app

A person can drop a folder from their computer onto the app (or pick one) and it becomes a workspace,
or a sub-folder of one they can write. Version-control internals, dependency and build folders, system
litter, the folder's own `.gitignore`, `.env` files and anything that looks like a key are never
imported, whatever these say; these three keys only set the size of what is.

<a id="folder_import_max_mb"></a>
### `folder_import_max_mb` / `folder_import_max_files` / `folder_import_file_max_mb` — how large a dropped folder may be

**Defaults `200`, `10000` and `25`.** Whole numbers: the megabytes one import may carry in all (at
most 2048), how many files (at most 100000), and the megabytes of any one file (at most 200). A
folder over either total is refused with the reason before a byte is sent; a single file over the
per-file cap is listed and left out. **Adversarial.** A larger cap is more disk per person and a
longer upload; a value that cannot be read refuses every folder import, naming the key, until it is
fixed.

## Features that are built but not yet proven

Some features are in this release switched off, because they have not been proven in real use yet.
They are switched as a whole, here or by the install, and nothing they stored is removed while they
are off. Off, a feature's buttons and pages are not shown, its tools are not offered to the agent, and
its routes answer `feature_disabled`. On again, it is all there again.

<a id="features"></a>
### `features` — which unproven features are on

**Default: empty, which leaves the install's answer.** The install answer comes from the environment
variable `VEXA_FEATURES`: empty (the release default) means every feature below is off, and a dev
stack sets `all`. One line here overrides it per feature, read on every request:
`features: {calls: on, notifications: off}`. The names are `shared_chats`, `notifications`, `calls`,
`folder_drop`, `chat_history_tools`, `calendar_write`, `workspace_forget`, `agent_messages` and
`intent_router` (the fast intent router: simple asks run without a full agent turn).
`crm`, `browser_bots`, `desktop` (which needs `crm`) and `desktop_cards` (which needs `crm` and `desktop`) run in services that do not read this
file, so only the install switches them. A name that is not a feature is logged and ignored. **Adversarial.** Switching a feature on
here puts something unproven in front of every person on this deployment. Switching one off hides
it, and keeps what it stored.
<a id="shell_timeout_sec"></a>
### `shell_timeout_sec` — how long one shell command of the agent may run

**Default: 120 seconds.** A shell command the agent runs is killed at this limit, together with
everything it started, and the agent is told it timed out and to narrow the command. A workspace or a
person may set a lower number in their own `POLICIES.md`, never a higher one; the most is 3600. A value
that is not a whole number is the default, never "no limit". **Adversarial.** A higher limit lets one
runaway command hold the chat's reply for that long; the person's Stop ends it at any time.

<a id="home_history_retention_days"></a>
### `home_history_retention_days` — how long the home's history is kept

**Default `forever`, like transcripts.** A whole number of days, or `forever`. The new-chat home
appends every composition it shows a person and every outcome on its cards (shown, clicked, sent
into a chat, dismissed and why, snoozed), and keeps beside each what Vexa has learned about that
person as a customer. Rows hold ids, titles and short reasons only — never mail, transcript or page
content — and each belongs to one person. This key drops a person's rows older than the number of
days on their next composition. **Adversarial.** A short window costs the ranking what it learned and
the person's answer to "what did you suggest last week"; a value that cannot be read keeps
everything, the safe direction for a history its owner can always ask to see.

<a id="home_cards"></a>
### `home_cards` — whether the home shows its composed cards

`off` (the default, for the release — founder 2026-10-15: "for now let's just disable these cards") or
`on`. Off, the new-chat home is the greeting, the composer, the starters and the People strip; nothing
is composed and no model is called for it. The install default is `VEXA_HOME_CARDS`.

<a id="home_people"></a>
### `home_people` — whether the home shows the People strip

`on` (the default) or `off`: everyone the person has interacted with, under the composer. The install
default is `VEXA_HOME_PEOPLE`.

<a id="external_images"></a>
### `external_images` — whether people's avatars and company logos are fetched

**Default `on`.** The home and every block view show a person's picture (their Gravatar, by a hash of
their address) and a company's site icon. agent-api fetches each one itself, through the same guarded
fetcher a page's pictures use, and caches it; the browser never asks a third party. `off` stops every
such fetch and initials are drawn instead. **Adversarial.** `on` tells Gravatar and a company's web
server that this deployment asked for a picture (never who asked, and at most once a week per
picture); `off` costs the faces.

<a id="presence_visibility"></a>
### `presence_visibility` — who sees whether a colleague is active

**Default `workspace_members`.** The desktop shows a small dot on a colleague's badge when they are
active, the way an activity status works on a social network. `workspace_members`: the members of a
workspace see that a colleague is active *in that workspace* — a chat of theirs working there was
active in the last ten minutes ("now") or the last day ("today"). `shared_chats`: a colleague shows as
active only from chats the two of you share. `off`: nobody's presence is shown. It is always coarse —
"now" or "today", never a time — never shown to a person about themselves, and never shown for a
person who switched their own presence off; this key can only narrow what each person allows. A
value that cannot be read is applied as `off`. **Adversarial.** `workspace_members` tells every member
of a shared workspace when a colleague works on it, which in a regulated organisation may itself be
sensitive; `shared_chats` or `off` costs colleagues the cue that someone is there right now.

<a id="lifecycles"></a>
### `lifecycles.card` — the stages a card moves through

**Default `To do > Doing > Done | Dropped`.** A card on the desktop or on a workspace's board is in one
stage of its type's lifecycle: the stages in order, separated by `>`, then any side states after `|`
(a card may move to a side state from any stage). A card starts in the first stage; it is finished in
the last stage or a side state. A person moves a card only to a declared stage, and **an agent never
moves one**: it proposes the move and a person accepts it (desktop ADR, founder 2026-10-15). A company
whose own card types exist names each one's lifecycle as `lifecycles.<type>` (lower case); a type with
none uses `lifecycles.card`. Fewer than two stages, a repeated name, or a name with a line break is
applied as the default and changes nothing. Read by crm-api through agent-api, within half a minute.
**Adversarial.** Renaming or dropping a stage leaves cards already in it where they are: they keep
their stage until a person moves them to one the lifecycle declares.

<a id="card_stalled_days"></a>
### `card_stalled_days` — when a card shows "Stalled"

**Default `14`.** A card that is not finished (not in its last stage or a side state) and has not
moved for this many days shows "Stalled" on its face. A whole number of days, 1 to 3650; anything else
is the default. Read by crm-api through agent-api, within half a minute. **Adversarial.** A short value
marks slow but healthy work as stalled; a long one hides work nobody is moving.

<a id="desk_limits"></a>
### `desk_placements_max`, `card_proposals_pending_max`, `card_proposals_per_agent_per_day`, `card_proposal_ttl_days` — how much the desktop holds

**Defaults `50`, `5`, `20` and `7`.** A person's desktop holds at most `desk_placements_max` placed
routines, chats, files and cards. An agent that may not arrange the desktop directly (a routine, an event agent, a
shared chat) only suggests a change, and the person applies or dismisses it: at most
`card_proposals_pending_max` suggestions wait for one person at a time, one agent session makes at most
`card_proposals_per_agent_per_day` a day, and a suggestion nobody answered is dropped after
`card_proposal_ttl_days` days. `0` on either suggestion count switches suggestions off. Each is a whole
number, and **a company can only narrow them**: a value above the default, one that is not a whole
number, or a lifetime below one day is applied as the default and named in crm-api's log. A desktop
already holding more than a narrowed limit keeps what it has and can be rearranged and thinned; it
cannot grow. They are read through agent-api, within half a minute of a change. Without agent-api the
defaults apply. **Adversarial.** Lower limits cost a person room on their desktop and their agents the
ability to suggest; the defaults let a busy agent put up to five suggestions in front of a person at
once.

<a id="budgets"></a>
### Budgets and models — what each person's agents may spend, and on which models

The install's configuration says which models are **available** (providers, endpoints, keys); these
keys say which are **allowed** and what the limits are. They are read on the next turn, no restart.
A person may set stricter limits for themselves, never looser. A value that cannot be read keeps the
install default — a typo never makes a budget unlimited. A model named here that the install does
not offer shows as *unavailable* in Company mode and is skipped.

- `budget_tokens_per_user_day` — tokens one person's agents may use in a rolling 24 hours, every kind of turn together. At 80% the person is told; at 100% chat stops with "ask your admin".
- `budget_tokens_routines_share` — the most of that budget, in percent, routines, event-started runs and the home may use; past it they pause, said on the bell and in the routine's chat.
- `routine_runs_per_day` — runs one person's routines may start in a day.
- `routine_min_interval_minutes` — the shortest schedule a routine may have.
- `home_compose_per_day` — model-ranked home compositions per person a day (past it the home is ranked without a model).
- `home_expand_per_day` — in-place elaborations per person a day.
- `home_cards` — whether the home shows its composed cards (`off` for the release: the greeting only; `VEXA_HOME_CARDS` overrides).
- `home_people` — whether the home shows the People strip.
- `event_agent_runs_per_day` — agent runs started by an outside event (mail, a meeting) per person a day.
- `invite_signin_days` — how many days the link in a workspace invite this deployment mailed signs its one address in with one click (never past the invite's own expiry; at most 30). `0`: the link never signs anybody in, and the invitee signs in the ordinary way first.
- `event_agent_max_trust` — the most an event-started run may do: `read`, `draft` or `act` (declared; not yet enforced).
- `model_chat_default` — the model a new chat starts on (empty: the catalog's default).
- `models_allowed` — the models anyone may pick, comma-separated catalog ids, or `all`.
- `model_routines` — the model routines run on; `cheapest` is the smallest allowed model the install offers.
- `model_home` — the model that ranks the home; `cheapest` by default, never an Opus-class model.
- `model_event_agents` — the model event-started runs use; `cheapest` by default.
- `appearance_accent` — the company's accent colour, until a person picks their own in Settings → Appearance: `clay` (the default), `blue`, `indigo`, `violet`, `teal`, `green`, `amber`, `rose`, `graphite`, or a `#rrggbb` colour (moved slightly if text on it would not read).
- `appearance_font_ui` — the company's interface font: `geist` (default), `system`, `inter` or `plex`.
- `appearance_font_reading` — the company's reading font for pages and messages: `ui` (same as the interface, default), `inter`, `plex` or `source-serif`.
- `models_for` — who may use which model: `model-id=all|someone@company.com|@company.com|group:name; …`. Empty: each catalog entry's own `allowed_subjects` still apply (their one-time seed).
- `model_rules` — the router's rules, one task type to one model: `{routines: cheapest, events: 'level:2', home: cheapest, chat: <id>}`. A value is `cheapest` (the allowed model with the lowest market price), a catalog id, or `level:N`. It overlays `model_chat_default`, `model_routines`, `model_event_agents` and `model_home`; a rule naming a model `models_allowed` leaves out routes to the person's default, never around the policy. Empty: those four keys decide, as before.
- `model_levels` — for hosted plans (the `levels` feature): the model each level 1 (fastest) to 5 (most capable) runs on, `{1: <id>, 3: <id>, 5: <id>}`. A level left out, or naming a model that is not allowed, is filled from the allowed models ordered by market price. Empty: all five come from that order.
- `model_intent_router` — the small model the fast intent router (the `intent_router` feature) asks when its rules cannot tell whether a short message is one simple ask: a catalog id or `cheapest`. Empty: `model_home`. It must answer within 0.7 s or the message goes to the full agent.
- `subscription_token_proxy` — whether a model subscription credential may reach agent workers: a Claude subscription token (`CLAUDE_CODE_OAUTH_TOKEN`, the claude CLI's credentials file) or Codex's `auth.json`. `deny` (the default): a turn that would run on one — the catalog's `auth: subscription`, or the deployment's own route with no API key — is refused with a message naming this policy, and no worker is given a subscription file. A consumer subscription is one person's licence and may not be proxied for others (see Model credentials & licensing). `allow`: those turns run on the subscription, for an install that serves the subscription's own holder. A person cannot change it.
- `model_scope_unpinned` — what an agent turn on a model-provider key that names no model may ask for. `runner_family` (the default): the models of the family its harness speaks, Anthropic's for Claude Code and OpenAI's for Codex and the openai-agent harness. `any`: whatever the key's endpoint serves. A comma-separated list of model ids: exactly those. A request for any other model is refused with `model_outside_scope`. A turn whose model is named (a catalog pick, Settings → Models) is held to that model whatever this says. A person cannot change it.

## What the agent may spend on model calls

Every agent turn — a chat message, a routine's run, a sensor's or an event agent's turn — is a set of
model calls this deployment pays for. These keys cap that spend in US dollars, per UTC day and per UTC
month, for the whole deployment and for each person. A turn is refused before it starts once any cap
that applies to it is spent; the person reads why and when it resets. Spend is counted from what
each call reports: the provider's own cost when it gives one, else its tokens at the two price keys.

<a id="llm_budget"></a>
### `llm_budget_org_daily_usd` / `llm_budget_org_monthly_usd` / `llm_budget_user_daily_usd` / `llm_budget_user_monthly_usd` — the caps

**Defaults `25`, `250`, `5` and `50`.** A number is a cap in dollars; `off` removes that one cap. A
value that cannot be read keeps the default, so a typo never lifts a cap. **Adversarial.** A cap
set too high spends up to the provider key's own credit limit; one set too low refuses turns people
need. A turn already running when a cap is reached finishes, so a cap can be passed by one turn.

### `llm_price_input_usd_per_mtok` / `llm_price_output_usd_per_mtok` — what a model no price table names counts at

**Default: empty.** A call whose provider reports no cost is counted at its model's market price:
OpenRouter's public price table, read once a day, or the snapshot bundled with the release when it
cannot be read. These two keys, in dollars per million input and output tokens, apply only to a
model neither table names (a self-hosted model of your own). Empty, such a model counts at the
base open-weight model's market price. They never override a market price.

### `credit_markup` / `credits_5h` / `credits_week` — Vexa credits (the `usage` feature)

**Defaults `1.3`, empty, empty.** With the `usage` feature on, every model call is debited in credits
(one credit is one US dollar): its market cost times `credit_markup` (default 1.3; a value below 1.0, or one that cannot be read, is refused and the default applies, so credits are never sold below cost). A person's
credits fill a 5-hour window and a weekly window, each opening at their first call. `credits_5h` and
`credits_week` are the plan's allowances; a full window refuses the next turn, with the time it is back,
unless the person turned on extra usage. Empty: no window limit.

### `llm_budget_notify` — who is told at 80% and 100%

**Default: empty.** User ids, comma-separated, who get an in-app notice the first time a cap reaches
80% and 100% in its period. Every crossing is also logged (`llm_budget_threshold`) and listed in the
admin panel, whatever this says.

## What listens to a live meeting — transcript signals

A small **signal model** can listen to a live meeting for what the meeting's agent asked it to (the
`listen:` brief on the meeting page, which the people in the meeting can read), and wake the agent
in the meeting's chat when it hears enough. The agent tunes the brief; these keys are the company's
part, and the agent cannot move them.

- `transcript_signals` — `off` (default) or `on`. Off, nothing listens and no signal wakes anyone.
- `model_signals` — the signal model: `{adapter: jev|openai-compatible, route: <model>, secret_ref:
  <broker ref>}`. Empty (default): no model, so nothing listens even when signals are on. With `jev`
  the transcript text goes to that model's provider; with `openai-compatible` it goes to the
  company's own endpoint and nothing leaves its network.
- `signals_wakes_per_meeting` — how many times one meeting may wake its agent per hour of the meeting (default `12`); past it the chat reads "Vexa paused listening: limit reached".
- `signals_cost_per_meeting_usd` — the most the signal model may cost per meeting (default `0.50`).
- `signals_default_brief` — the brief a meeting starts from before its agent writes one (empty: the
  built-in brief: decisions, action items, and anything addressed to Vexa).

**Adversarial.** Anyone in a call can speak instructions. A turn a signal starts is untrusted: it
writes only the meeting's page and its brief, and everything else it may only propose.
`addressed_to_vexa` is always in the brief and always wakes the agent; it cannot be removed.

## How an answer here is decided, and recorded

The block at the top of this file can be edited by hand — it is a file, and every edit to it is a
commit in `_global` with an author. It can also be arrived at by **the policies wizard**
(`asks/policies-wizard.md`), which is what the **Set up policies** act on this page runs and what
the setup conversation calls: five questions about who is in this company's meetings and what it is
afraid of, a recommendation derived from the answers with each rule's reasoning taken from the
sections above, and — only on a yes — the block written and the choice recorded.

**Every decision is APPENDED, under `## Decision`, at the foot of this file, and no decision is ever
rewritten.** Each one carries its date, who took it, the five answers in their own words, the
profile and the overrides. Running the wizard again adds a new section below the last; the old one
stays as it was written, including where today disagrees with it. The record answers *why we started
here*, and a record edited to agree with the present answers nothing.

## What the administrator is authorized to do, and where that is written

A rule above says what this deployment does. A **standing authorization** says what its
administrator may change without being asked again — and it is recorded here, at the foot of this
file, in `## Authorization` sections, on exactly the terms `## Decision` sections are kept: appended
in the administrator's own words, dated, and never rewritten.

The first of them is **writing flows** (`asks/flow-author.md`). Founder, 2026-09-06: *"we want to be
able to write flows for the global chat as we like."* The administrator says what this deployment
should do; the agent maps that sentence onto a trigger and an ordered list of steps from the
vocabulary the image carries, shows it as the page it will become, and submits it. A grant recorded
here is not asked for a second time.

**A standing authorization is not a confirmation, and it does not replace one.** The grant answers
*may you write flows at all*; every activation is still confirmed once, on the flow shown as its own
page. One is about the person's authority and is asked once; the other is about this particular
change and is asked every time.

## Where each rule is read today

Written down rather than left to be discovered. A rule that nothing reads is a control that silently
does nothing, which is worse than one that is not there — so this table says which are enforced by
code today and which are, for now, a written commitment the setup conversation records and the
product does not yet check.

| rule | read by |
|---|---|
| `agent_reads_desk` | **declared, not yet enforced** — no access path reads it, and the disclosure says what the software does whatever it answers |
| `report_to_participants` | the attendee fan-out (`email_attendees`) |
| `external_participants` | whether the fan-out writes to a participant with no account (`email_attendees`) |
| `attendee_domains` | the line between inside and outside, for the same fan-out |
| `data_statement` | the provenance line above every minutes mail, and the locality clause of the disclosure in every post-meeting mail |
| `prep_and_invite_mail` | the prep note before a meeting (`emit_prep`) |
| `minutes_mail` | the minutes mail after a meeting (`email_minutes`, and its group fan-out) |
| `mail_domains` | the inbound mailbox: which senders and invitation organisers it acts for |
| `transcript_retention_days`, `recording_retention_days` | **declared, not yet enforced** — nothing deletes on a schedule, and the disclosure names no number of days |
| `bot_joins_on_invite` | the inbound mailbox: off, an invitation is recorded and nothing is created, accepted or joined |
| `bot_joins_mixed_meetings` | **declared, not yet enforced** — admission would have to read this file before dispatching a bot |
| `organizer_confirms_join` | **declared, not yet enforced** — and it is `off`, which is today's behaviour, so the line records the gate rather than claiming it |
| `agent_writes_pages` | **declared, not yet enforced** — containment already bounds where a turn writes; this switch does not yet gate whether it writes |
| `newcomer_reads_history` | **declared, not yet enforced** — and it is `off`, which is the safe direction to be unenforced in |
| `global_admin_only` | **structural** — the `_global` mount is read-only except for the admin account, which identity names; this line records it rather than deciding it |
| `open_web` | **declared, not yet enforced** — the agent's web tools are chosen at dispatch, not read from here |
| `user_flow_steps` | flows-api, when a person submits a flow of their own (`POST /flows`, `flows_submit`) |
| `routine_unattended_actions` | flows-api when a person saves a routine, and the flows worker at every run (`flows_steps.grants`); meeting-api and flows-api then honour only the verbs the run carries |
| `routine_pauses_when_shared` | the flows worker, at the end of every routine run refused `shared_conversation` (`flows_steps.routine`) |
| `agent_messages_own`, `agent_messages_shared`, `agent_messages_from_routines` | flows-api's intake, for every `thread.message` (`message_agent`) |
| `session_agent_speaks` | agent-api, on every message posted to a chat with two or more people in it (`session_access`) |
| `llm_budget_*`, `llm_price_*`, `llm_budget_notify` | agent-api before every turn is dispatched (`llm_budget`), and its meter after every model call |
| `connection_actions`, `connection_actions_unattended` | agent-api before every Gmail and Calendar action (`connection_policy`), and the credential broker again |
| `calendar_notify_attendees`, `calendar_attendee_domains`, `calendar_ids` | the same two, on every calendar change |
| `draft_recipient_domains`, `draft_attachments`, `draft_attachment_max_kb` | the same two, on every draft created or updated |
| `post_meeting_auto_accept` | agent-api, when the post-meeting run records a change proposed after a call (`routers/calls.py`) |
| `folder_import_max_mb`, `folder_import_max_files`, `folder_import_file_max_mb` | agent-api, on every folder dropped or picked into the app (`folder_import`), at the plan and again as the bytes arrive |
| `features` | agent-api, on every request to a switchable feature's route, on its served tool list, and on `/api/features`, which the app reads to hide what is off (`features.py`) |
| `home_history_retention_days` | agent-api, on every home composition (`home`), pruning that person's compositions and card events |
| `external_images` | agent-api's picture route (`home_media`) before every logo fetch, and the workspace image routes before a `site`/`url` fetch (`workspace_icon`) |
| `home_cards` | agent-api's home (`GET /api/home`, `POST /api/home/compose`): off = the greeting only, no composition, no `home.stale`, no model call |
| `home_people` | agent-api's home: whether the terminal draws the People strip |
| `presence_visibility` | agent-api's desktop (`routers/desktop.py`), on every desktop read; a person's own presence switch is applied after it |
| `desk_placements_max`, `card_proposals_pending_max`, `card_proposals_per_agent_per_day`, `card_proposal_ttl_days` | crm-api's desk (`core/crm/src/crm/policies.py`), on every arrangement and suggestion, read through agent-api and kept 30 seconds |
| `lifecycles.card` (and `lifecycles.<type>`) | crm-api's cards (`core/crm/src/crm/card_ops.py`), on every card write and read, read through agent-api and kept 30 seconds |
| `card_stalled_days` | crm-api's cards (`core/crm/src/crm/card_ops.py`), on every card read, read through agent-api and kept 30 seconds |
| `budget_tokens_per_user_day`, `budget_tokens_routines_share` | agent-api before every turn (`budgets`): chat, routine runs, event-started runs, the home |
| `routine_runs_per_day`, `routine_min_interval_minutes` | flows, when a routine run is admitted and when a routine is created (read live) |
| `home_compose_per_day`, `home_expand_per_day`, `model_home` | agent-api's home (`home`, `home_model`) |
| `event_agent_runs_per_day` | agent-api's event door (`routers/ingress.py`) |
| `invite_signin_days` | agent-api's invite act (`routers/sharing.py` → `membership_acts.invite`), when a mailed invite is minted |
| `event_agent_max_trust` | **declared, not yet enforced** |
| `appearance_accent`, `appearance_font_ui`, `appearance_font_reading` | agent-api's `GET /api/user/appearance` — the company default under each person's own choice |
| `model_chat_default`, `models_allowed`, `models_for`, `model_routines`, `model_event_agents` | agent-api's model catalog (`model_providers/catalog.py`) and dispatch, on every turn |
| `subscription_token_proxy` | agent-api's dispatch, where a turn's model route is decided (`dispatch.apply_model_route`); the runtime mounts a subscription file only into a worker on the route it allowed |
| `model_scope_unpinned` | agent-api's dispatch, when it seals a turn's model route (`model_proxy.confine`); the model proxy holds every request of the turn to it |
| `transcript_signals`, `signals_wakes_per_meeting` | agent-api's event door, before a `meeting.signal` wakes a meeting's agent (`routers/ingress.py`), and the `signals` service |
| `model_signals`, `signals_cost_per_meeting_usd`, `signals_default_brief` | the `signals` service, read through agent-api's `GET /internal/meetings/{row}/listen` |

Every line marked *not yet enforced* is a gap in the software, not in the policy. It belongs in
[`MISSING.md`](MISSING.md) if this deployment is relying on it.

## Which mail is sent — `mail.<kind>` and `mail_caps`

Every mail Vexa sends is a kind, and each kind has one row above: `on`, `off` or `admins` (only the
admin account and instance admins receive it). **What it changes:** whether that mail leaves at all.
**Adoption:** a mail people did not ask for teaches them to ignore the next one; the shipped answer
is on only for mails a person triggered by pressing a button (sharing, call invites) and for the
sign-in link. **Security:** every attempt is recorded (kind, template version, recipient domain,
result); a test domain is never relayed. **Adversarial:** a hostile admin could switch on a noisy
kind — `mail_caps` (`minutes=50, *=500`) and the operator's own ceiling bound how much. A kind is
switched on only after its current wording was test-sent to the admin account (`mail_kind_set`
refuses otherwise), and `mail.sign-in` cannot be switched off. The wording is
`_global/mail/<template>.md`; design it from the chat (`mail_templates_list`, `mail_template_edit`,
`mail_preview`, `mail_test_send`, `mail_kind_set`).
