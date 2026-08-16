# presend-gate — never email an artifact for a record that is not a meeting

A meeting artifact is mailed to **everyone on the invite**. That is the product. It is
also the failure mode: if the record was not a meeting, the mail goes out anyway, to
people who were never in it, containing whatever the microphone picked up.

This brick is the check that runs before the send. It is pure, stdlib-only, and has no
I/O — give it a record, it returns a verdict and the recipient list you are allowed to
use.

## Why it exists

Rendering artifacts over 22 real recordings from one archive (2026-08-16 dogfood):
**2 of them were not meetings.** One was an hour of played-back video audio; one was a
forgotten bot recording a private household morning. Both produced structurally valid,
well-formed, entirely sendable artifacts, and nothing anywhere in the pipeline noticed.
A single classifier gap here is not a quality problem — it mails someone's family
conversation to a distribution list.

## Three outcomes, never two

| Outcome | Who receives the artifact | When |
|---|---|---|
| `send` | every participant | **positive** evidence of ≥2 parties taking turns, no risk flag |
| `hold_for_creator` | the meeting's creator, alone, with a plain note | anything uncertain — **the default** |
| `suppress` | nobody (the recording itself stays in the creator's list) | affirmative evidence of a non-meeting |

Uncertainty never broadcasts. `sensitive_context` — a record soaked in domestic
vocabulary — forces at least a hold regardless of every other signal.

## Use

```python
from presend_gate import from_transcript_payload, gate

record = from_transcript_payload(
    payload,                      # the meeting-api transcript payload
    creator=workspace_owner_name, # who invited the bot
    bot_names=[bot_display_name], # what the bot calls itself in the meeting UI
    roster=invite_attendees,      # the invitation's attendee list, when known
    roster_source="invite",
)
verdict, recipients = gate(record, participants=invite_emails)
for address in recipients:        # () when suppressed, (creator,) when held
    send_artifact(address, artifact, note=verdict.note)
```

`route_recipients` is the policy hook. **A pipeline that mails its own participant list
has bypassed the gate** — the recipient list must come from here.

Thresholds live in one visible `Policy` dataclass and are constructor arguments, so an
operator can retune hold-rate without touching the logic.

## What it measures

The spine is *interleaving*, not speaker count. Speaker count is the obvious signal and
the wrong one: attribution collapses regularly, and a real meeting whose attribution
collapsed to one label is indistinguishable from a monologue by that measure.

| Signal | What it catches |
|---|---|
| `dialogue_window_share` | ≥2 parties in the same slice of the record — the strongest single separator |
| `alternation_rate` | speaker changes per adjacent segment: two solid blocks are two audio sources, not a conversation |
| `monologue_ratio` | longest unbroken single-voice stretch over total speech |
| `bot_speaker_present` | the bot's own display name attributed as a speaker ⇒ tab/room playback was captured |
| `counterparty_count` / `counterparty_known` | did a roster that *would* have named someone else name nobody? |
| `substantive_speaker_count` | a second voice that only says "mm-hm" is not a second party |
| `domestic_rate` | the `sensitive_context` probe (en/ru/de) — can only ever force a hold |
| `speech_density`, `language_switch_rate`, `top_speaker_share`, `second_person_rate` | soft flags; two or more force a hold, one never does |

**Absence of a roster is not evidence of absence.** Three real meetings in the
calibration corpus carry no roster at all, so "no roster" alone can never suppress —
only a roster that *would* have named a counterparty and names none.

## Honest limits

- **n=22, of which 2 are negatives.** Every threshold is a hand-set heuristic with a
  margin, not a learned parameter. Read [`docs`](#calibration) below and retune from
  production hold-rate.
- **Asymmetric on purpose.** Holding a real meeting costs one person one click. Emailing
  a private conversation cannot be undone. Where the two trade off, this errs to hold.
- **One accepted false positive in the corpus:** a real FINOS TOC call whose attribution
  collapsed to a single label, with no roster. It holds. That is the correct call on the
  evidence, and it is the number to watch — if attribution collapse is common in
  production, hold-rate rises with it.
- **The lexical probes cover English, Russian and German.** On other languages they read
  ~0. They are wired so that a 0 can only ever withhold, never authorize.
- **This is not a content-safety classifier.** It answers "was this a meeting between
  people", nothing else.

## Calibration

Margins measured on the 22-record corpus (worst real meeting · threshold · worst
non-meeting):

| Threshold | worst real | set to | worst non-meeting |
|---|---|---|---|
| `min_dialogue_window_share` | 0.450 | **0.25** | 0.000 |
| `min_alternation_rate` | 0.128 | **0.03** | 0.000 |
| `max_monologue_ratio` | 0.193 | **0.35** | 0.566 |
| `sensitive_domestic_rate` | 0.003 | **0.12** | 0.222 |

The corpus is **private and not vendored** — one of its records is the family
conversation. `tests/test_corpus.py` replays it when `VEXA_PRESEND_CORPUS` points at an
operator-held copy, and skips otherwise; `tests/test_gate.py` covers every branch with
synthetic records built to the shapes measured from it.

```bash
uv run pytest -q                                             # branch coverage, always runs
VEXA_PRESEND_CORPUS=/path/to/corpus uv run pytest -q         # + the real replay
python -m presend_gate.report <dir> --creator NAME --bot NAME # the results table
```
