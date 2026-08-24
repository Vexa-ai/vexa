# `m365_rig.py` — the Microsoft 365 half of the witness ladder, hands-free

`mail_real.py` is the Gmail/IMAP world: a human types a calendar invite into their own
client and the flows mailbox watches it arrive. That is unrepeatable — it needs a human, a
browser and a Google account. This rig is the same three facts over Microsoft Graph, driven
entirely from a terminal, so the intake path can be exercised on every run.

```bash
cd core/flows
python3 witness/m365_rig.py whoami                   # what the credential can do here
python3 witness/m365_rig.py meeting create --lobby-bypass
python3 witness/m365_rig.py invite send --to info@vexa.ai --start-in 20
python3 witness/m365_rig.py gmail poll --since-uid <N> --require-ics
python3 witness/m365_rig.py mail sent-ics --subject-contains "probe"
python3 witness/m365_rig.py cleanup                  # ALWAYS, before you walk away
```

## What each verb proves

| Verb | Proves |
|---|---|
| `whoami` | The app-only token carries the four application roles, the tenant matches the vault, and the mailbox is readable — i.e. admin consent and the Exchange ApplicationAccessPolicy are actually in force. Run this first in any new tenant. |
| `meeting create` | `OnlineMeetings.ReadWrite.All` + the Teams ApplicationAccessPolicy work: a real Teams meeting exists, with a `joinWebUrl` that parses to the `(platform, native_meeting_id)` pair `POST /bots` wants, and `lobbyBypassSettings.scope = everyone` so a bot is admitted without a human. |
| `invite send --to <addr>` | Exchange composes and **delivers** a real invitation with a real ICS. This is the generator for the flows intake path — the thing a customer's employee does when they invite the Vexa mailbox. |
| `gmail poll` | The delivery proof. Polls an external mailbox we control (`vexa-mail` vault, IMAP) and reports which ICS properties carry the Teams link. |
| `mail poll` | Reads the tenant mailbox over Graph — the live counterpart of the Graph mail transport. |
| `mail sent-ics` | Pulls the raw MIME of the **sent** copy. A source of ICS *bytes*, never a delivery proof. |
| `cleanup` | Consumes the ledger and removes every meeting and event the rig created. Events are **cancelled** (not deleted) so no invitee is left holding a phantom meeting. |

Everything the rig creates is appended to `.m365-rig-ledger.jsonl` next to the script, so
`cleanup` finds it again after the process is gone — including after a crash. The ledger is
gitignored: it is live tenant state, not source.

## Tenant shape

One Microsoft 365 Business Basic tenant, one licensed mailbox, one app registration
(`vexa-flows-m365`) holding **application** permissions with admin consent:
`Mail.ReadWrite`, `Mail.Send`, `OnlineMeetings.ReadWrite.All`, `Calendars.ReadWrite` — plus
an Exchange ApplicationAccessPolicy scoping the app to that one mailbox and a Teams
ApplicationAccessPolicy permitting meeting creation on its behalf. Credentials live in
`~/dev/vexa-secrets/business/m365-graph.enc.env` and are decrypted into process env only —
never printed, never written to disk. The customer-facing version of this setup is the
admin runbook in `vexa-delivery` (`docs/environments/microsoft365.mdx`).

## Findings — measured live on 2026-08-24

### 1. Exchange suppresses self-delivery

Inviting the organizer's **own** mailbox produces no inbox message. Exchange recognises the
organizer as the attendee, writes the event straight into their calendar, and delivers
nothing. `mail poll` on the organizer's inbox is therefore empty no matter how long you
wait, and **that emptiness is not a transport bug** — the natural next move (wait longer,
then suspect the poller, then suspect the permissions) chases three phantoms in a row.

Two honest routes out, and they prove different things:

* **Delivery** — send to a *different* mailbox we control and poll there (`invite send --to
  <external addr>` then `gmail poll`). This is the only thing that proves an invitation
  travelled.
* **Bytes** — read the sent copy's MIME (`mail sent-ics`). This proves what Exchange put on
  the wire and nothing about arrival. Labelled as such in the verb's help, because a rig
  that quietly calls it a delivery proof is worse than a rig that fails.

### 2. Where the Teams link actually is in the ICS

This is what a calendar parser has to be written against. Property names below are verbatim
from a live Exchange-composed `METHOD:REQUEST` invitation; the values are redacted.

| Property | Carries the join URL? | Notes |
|---|---|---|
| `LOCATION` | **No** | Literal `Microsoft Teams Meeting`. The most likely wrong guess. |
| `X-MICROSOFT-LOCATIONS` | **No** | JSON, `DisplayName: "Microsoft Teams Meeting"`, `LocationUri: ""`. |
| `X-MICROSOFT-SKYPETEAMSPROPERTIES` | **thread id, decoded** | `{"cid":"19:meeting_…@thread.v2","rid":0,"mid":0,…}` — **prefer this**: no URL parsing at all. |
| `X-MICROSOFT-SKYPETEAMSMEETINGURL` | **Yes, canonical** | The full `/l/meetup-join/19%3ameeting_…%40thread.v2/0?context=…`. One value, unambiguous, machine-written. |
| `DESCRIPTION` | **Yes — twice, and the first is wrong** | See below. |
| `X-MICROSOFT-SCHEDULINGSERVICEUPDATEURL` | incidentally | Host is `api.scheduler.teams.microsoft.com`; embeds `19_meeting_…@thread.v2` with an **underscore**, not a colon. |
| `X-MICROSOFT-ONLINEMEETINGINFORMATION` | no URL | `{"OnlineMeetingProvider":3}` — `3` = `teamsForBusiness`. The reliable "this is a Teams meeting" flag. |

`DESCRIPTION` is the trap. Unfolded and redacted:

```
DESCRIPTION;LANGUAGE=en-US:<body text>\nMicrosoft Teams meeting
  \nJoin: https://teams.microsoft.com/meet/373241627805208?p=XXXXXXXXXXXXXXXXXX
  \nMeeting ID: 373 241 627 805 208\nPasscode: XXXXXXXX
  \nHelp: https://aka.ms/JoinTeamsMeeting?omkt=en-US
  \nSystem reference: https://teams.microsoft.com/l/meetup-join/19%3ameeting_…%40thread.v2/0?context=…
  \n________________________________
```

Three consequences for the parser:

* **First match ≠ right match.** The `Join:` line now carries the **short form**
  `teams.microsoft.com/meet/<15 digits>?p=<passcode>`, which parses to native id
  `373241627805208`. The `19:meeting_…@thread.v2` form only appears further down after
  `System reference:`. A first-match regex over `DESCRIPTION` silently produces a different
  meeting identifier than the same event's `X-` property.
* **`\n` is two characters.** Newlines inside an ICS `TEXT` value arrive as a literal
  backslash-n, so a URL character class that permits `\` runs straight past the end of the
  link into the next line's prose (`…?p=Hsp…\nMeeting`). Exclude the backslash.
* **Unfold before matching, always.** Exchange folds every line at 75 octets and a Teams
  join URL is ~200 characters, so it arrives split across three physical lines each starting
  with one space. A regex over the raw text matches only the first fragment and yields a
  truncated, unjoinable URL.

Recommended precedence: `X-MICROSOFT-SKYPETEAMSPROPERTIES.cid` →
`X-MICROSOFT-SKYPETEAMSMEETINGURL` → `DESCRIPTION`'s **last** `meetup-join` match. Never
`LOCATION`. `witness/m365_rig.py:ics_teams_evidence()` implements exactly that, and
`tests/test_m365_ics.py` pins all of it offline against a captured real invitation — those
tests are the executable form of this section.

### 3. Exchange RFC 2047-encodes the subject on any non-ASCII character

An em dash is enough: the subject arrives as
`=?Windows-1252?Q?Vexa_rig_=97_ICS_property_probe?=`. `mail_real.poll` hands headers through
raw, so a substring match on `msg.subject` silently never fires — it cost one four-minute
polling window that reported `FAIL` while the mail sat in the inbox the whole time. The rig
decodes with `decode_header_text()` before matching.

## Rules of the rig

* **Always `cleanup`.** The tenant is a shared asset; a rig that leaves residue is a rig
  nobody runs twice. `cleanup --dry-run` shows what it would remove.
* **`onlineMeetings` needs the organizer's objectId GUID**, not the UPN — the UPN yields a
  misleading `not a valid GUID` / `Resource not found for the segment` error. The rig reads
  `VEXA_GRAPH_ORGANIZER_ID` for exactly this.
* **Never print a secret value.** `load_creds()` decrypts into process env; nothing in the
  rig echoes a credential.
* A failed Graph call is a **result the rig reports**, not a crash: `Graph.call` returns
  `(status, payload)` and never raises on an HTTP error.
