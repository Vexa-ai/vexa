# artifact-pipeline (dev v0)

**A completed meeting becomes a gated, per-participant context delta, and is delivered.**

This is the arrow: `workspace × meeting → artifacts`, where an artifact is *the rendered
context delta for ONE person* — what changed in their context because this meeting happened.
Every other piece of the loop existed before this service and nothing joined them: the
record, the pre-send gate with no caller, the postman taking a hand-written file.

```
trigger ──▶ gather ──▶ GATE ──▶ render ──▶ deliver ──▶ record
  id        the         send /   one        the         append-only
  (CLI;     record      hold /   artifact   postman     run log =
  webhook   over        suppress per         (magic     audit +
  later)    HTTP        + who    person      link)      idempotency
```

## The six stages, and the port behind each

| # | Stage | Port | Ships today | Reversible because |
|---|---|---|---|---|
| 1 | **Trigger** | `MeetingSource` | `ListSource` — ids from the CLI | the `meeting.completed` webhook fills the same `CompletedMeeting`, which already carries workspace, creator and invite roster |
| 2 | **Gather** | `MeetingGateway` | `HttpMeetingGateway` — the public REST surface with an API key | a `CorpusTransport` serves the same routes off disk for dev; nothing imports `meeting_api` |
| 3 | **Gate** | *(the brick itself)* | `PreSendGate` composing `modules/presend-gate` | policy thresholds are the brick's, tunable there |
| 4 | **Render** | `Renderer` | `TemplateRenderer` — deterministic, no model | `LlmRenderer` is declared and raises until BYOT lands |
| 5 | **Deliver** | `Delivery` | `FileDelivery` · `CommandDelivery` (the postman) · `NullDelivery` | the postman is one configuration of a generic command sink |
| 6 | **Record** | `RunLog` | `JsonlRunLog` · `MemoryRunLog` | one append-only stream is both the audit trail and the idempotency oracle |

Four properties are not negotiable and are asserted in tests:

- **Nothing bypasses the gate.** The recipient list comes from the brick's
  `route_recipients`, never from the participant list. **Gate runs before render**, so a
  record that is not a meeting never produces an artifact at all — there is nothing on disk
  for a later code path to send by accident.
- **The record's own id, everywhere.** The artifact header, the magic link's scope and the
  idempotency key all use the id the *payload* states, never the id that was requested.
- **Idempotency is per (meeting, recipient)**, read back from the run log. Only `sent` is
  terminal; `no_address` and `failed` retry.
- **The artifact has a schema.** One document shape, whatever renders it — see below.

## The artifact is a schema, not a prose contract

Two renderers built against a prose description emitted structurally different headers in one
night — one bold, one plain, with the date line unkeyed. The postman read only the bold form,
fell back to the **directory name** for the record id, and mailed magic links pointing at the
wrong meeting (5174 where the artifact was about 5175), subject degraded to a bare
`meeting 5174`. Nothing errored.

`artifact.py` is the fix: `Artifact` / `Section` / `Recipient` with a canonical markdown
emitter and symmetric serialization. The header keys and the section order live in
`labels.py`, not in any renderer, so a renderer chooses *what a section contains* and never
*what it is called or where it sits*.

```
**To:** Marvin Hanke                    │  **Кому:** Алексей Рогов
**Meeting:** 2026-05-18 · Teams · 61m   │  **Встреча:** 2026-05-05 · Teams · 79 мин
**record:** 12615 · [open the record](#)│  **запись:** 11706 · [открыть запись](#)
```

`record_link` is a **slot**: empty renders the placeholder the postman rewrites; set renders
the real link and drops the marker. Both are the same document.

## The two renderers, and the line between them

`TemplateRenderer` emits **only what a cue proves** — a decision phrase, a first-person
undertaking, a question. Four sections, each quotable back to a sentence someone actually
said: `Decided` · `You committed to` · `Owed to you` · `Asked of you`. It does not judge
salience and it does not infer.

`LlmRenderer` is the one the product wants — the archive's highest-value lines were derived
deltas nobody said aloud — and it **raises**. It needs the workspace's own model route
(BYOT), the BYOT decision is open, and a stub that silently fell back to the template would
make a run log say "model" while producing cue-matched sentences.

Two limitations, both upstream and both stated rather than papered over: speaker attribution
collapses in interview-shaped meetings (the interviewer's questions filed under the
interviewee), and "in the conversational neighbourhood" is a proxy for "said to you" that is
right in a two-party call and looser in a six-party one.

## Run it

```bash
cd core/meetings/services/artifact-pipeline

# against a deployment
PYTHONPATH=src:../../modules/presend-gate/src uv run python -m vexa_artifact_pipeline \
  --meeting 12615 --creator "Dmitry Grankin" --bot-name "Vexa test" \
  --base-url http://127.0.0.1:18056 --api-key "$VEXA_API_KEY" \
  --out out/artifacts --run-log out/runs.jsonl

# against harvested records, no server (dev source; the run log says so)
PYTHONPATH=src:../../modules/presend-gate/src uv run python -m vexa_artifact_pipeline \
  --corpus /path/to/corpus --meeting 12615 --creator "Dmitry Grankin" \
  --out out/artifacts --run-log out/runs.jsonl

# deliver by email through the chat-door postman (share CHAT_DOOR_SIGNING_KEY with the door)
... --postman ../chat-door --door-base-url http://127.0.0.1:8087 \
    --smtp-host 127.0.0.1 --smtp-port 11025 \
    --address "Hanke, Marvin=marvin@example.test"
```

`presend_gate` is a composed brick and is not on the default path — the `PYTHONPATH` above
is what wires it. The gate raises a named `ModuleNotFoundError` rather than degrading,
because there is no correct behaviour for "send without the gate".

## What is not built

- **No real trigger.** The `meeting.completed` webhook does not exist; the CLI stands in.
- **No model renderer.** BYOT is undecided; `LlmRenderer` raises.
- **No HTTP surface, no Dockerfile, no Helm.** This is a job, not a server, in v0.
- **No participant surface on the control plane.** The invite roster reaches the gate only
  when a trigger hands it over; today that is the CLI or the mailroom binding by hand.
- **Delivery rides the postman's CLI**, so a change to its flags breaks this at run time,
  not at import time. `tests/test_postman_contract.py` is where that contract is checked.

## Tests

```bash
uv run pytest -q                                    # no docker, no network, no model
VEXA_ARTIFACT_CORPUS=/path/to/corpus  uv run pytest tests/test_corpus_pipeline.py -q -s
VEXA_CHAT_DOOR_SRC=/path/to/chat-door/src uv run pytest tests/test_postman_contract.py -q
```

The gate is never faked. The gateway is faked at the *transport*, so the shipped HTTP client
is the code under test. See [`tests/README.md`](tests/README.md).
