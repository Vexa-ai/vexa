# Background jobs — the contract both runners keep

A turn that takes two minutes holds the chat for two minutes. On 2026-09-06 the founder pressed
Create and Extend four times in the minutes panel; the agent made 38 tool calls across those four
acts and the composer was busy throughout — he could not ask anything until each act landed
(`Vexa-ai/vexa#1584`). The act is not the problem. Running it *inside the turn* is.

A **job** is agent work that runs OUTSIDE the turn loop: the turn returns at once with one short
line, the job runs on its own thread with its own harness session and its own step count, and its
result arrives later as a line in the chat and a refreshed page.

## What is a job, and who decides

| | Who asks | How |
|---|---|---|
| **The client's act** | the pages panel's Create / Extend | `chat_intents.JOB_KINDS` — the control plane prefixes the composed preset with the job mark |
| **The agent's own long act** | the model, mid-turn | the `spawn_job` tool (`openai-agent` only today — see *Both runners*) |

Both arrive at the same place. **The job runner lives in the worker, ABOVE the harness**
(`worker/jobs.py`), so it is one implementation for every runner rather than a per-adapter feature
that has to be written three times and stays right in one of them.

## The mark

`shared/marks.py` gains a third mark beside the two chat marks, for the same reason they exist: the
decision has to be recognisable in the RECORD rather than guessed from prose.

```
[vexa-job:<kind>:<target>]
```

Written by `control_plane/routers/chats.py` onto `body.prompt`, on the same carrier `SILENT_PREFIX`
rides, and read by `worker/engine.py`'s `run_message`. It is **searched for, not matched at the
start**: `_context_grounding` prepends the grounding and the context sentinel before the worker ever
sees the string, so the mark sits mid-prompt by construction. Reading it strips it and leaves
everything else — the job runs the whole composed prompt, grounding included.

It is applied **outside** the preset branch, deliberately. Whether an act blocks the chat must not
depend on whether the preset library is current: a deployment one release behind the terminal falls
back to the client's plainer sentence, and that sentence takes exactly as long to run.

The terminal never writes this literal (the server does), so unlike `MACHINERY_MARK` there is no
TypeScript copy and nothing for `gate:fact-parity` to keep honest.

## The event vocabulary

Jobs ride the channel turns already ride: `XADD unit:<id>:out`, one field `event`, relayed verbatim
by `RedisStreamReader` → `_sse` → the browser. Four new types, additive to the frozen five:

| event | emitted by | fields |
|---|---|---|
| `job-started` | the turn that asked | `job_id`, `kind`, `target`, `line`, `turn_id` |
| `job-refused` | the turn that asked | `kind`, `target`, `line`, `turn_id` — **no** `job_id` |
| `job-progress` | the job's thread | `job_id`, `window`, `calls`, `line` — a window ended and a fresh one opened |
| `job-done` | the job's thread | `job_id`, `kind`, `target`, `line`, `ok: true` |
| `job-failed` | the job's thread, or `serve()` at boot | `job_id`, `kind`, `target`, `line` (+ `ok: false` when a job produced it) |

**Every one of them carries `session`** — the chat that owns the job (`Vexa-ai/vexa#1613`). See
*A job belongs to one chat* below for why that is not decoration.

and **every event the job's own turn yields is tagged `{**ev, "job_id": …}` and carries no
`turn_id`.** That is the whole of "progress reaches the terminal tagged with a job id": a job's
`tool-call`s are the job's step count, and a consumer that keys on `turn_id` cannot mistake them for
the chat turn's.

The turn that spawns a job emits, in order: `turn-accepted` · `job-started` (or `job-refused`) ·
`message-delta` carrying that event's own `line` · `turn-complete`. **It runs no model call at
all** — the acknowledgement is composed by the runner, not asked for. A turn that had to ask a model
for its own *"I'll say when it's there"* would be the wait it exists to remove, at a tenth of the
length.

## The connection stays open

`RedisStreamReader.read` closes the view on `turn-complete`. A job outlives its turn, so the reader
now closes on `turn-complete` **only when no job it saw start is still open**:

```python
if t == "job-started":            open_jobs.add(job_id)
elif t in ("job-done","job-failed"): open_jobs.discard(job_id)
elif t == "turn-complete":        turn_done = True
if turn_done and not open_jobs:   return
```

With no job in play this is byte-for-byte today's behaviour. The client half matches, in the same
two lines: `chatStream` keeps a SET of the jobs it started (a marked act spawns one, a turn calling
`spawn_job` twice spawns two), calls the turn finished only when `turn-complete` has arrived and
that set is empty, ignores every `job_id` it did not start (a second connection reading the same
stream must not fold another job's steps into its turn), and does not let a job's `commit` end the
turn.

A connection that attaches mid-job (a reconnect) never saw `job-started`, so it holds until the next
`turn-complete` or the reader's 10-minute idle give-up — it keeps receiving the job's events either
way, which is the behaviour worth having.

## A job belongs to one chat

Founder, 2026-09-06 14:10Z, opening a NEW EMPTY chat and finding two lines about another chat's
jobs as its first content: *"some leak to empty chat"*.

The cause was not the stream. Streams are already per `(subject, session)`. It was the **register**:
`<chat_root>/.claude/jobs/` sits under the person's continuity root, which every one of their chats
shares, and `cancelled_at_boot()` read the whole directory. So a worker booting for chat B found
chat A's records — very often jobs that were **running right now** — announced them on B's stream as
its own restart casualties, and deleted them on the way past, taking A's ability to report them when
they really did die. Measured on the dogfood stack the same day: `j-58b3833e` reported in
`pchat-mtppgd4w` where it ran and again in `pchat-mtpphl4o` where it did not; `j-192ed731` the same
across `meet-147` and `pchat-mtpthvmp`.

Two halves, and both are needed because they fail differently:

- **The register names its owner.** Each record carries the session that wrote it; the boot scan
  reports and deletes only its own, leaves a foreign one exactly where it is, and silently cleans up
  a record from before the field existed (there is nothing truthful to say about whose that was). A
  runner given no session at all is unchanged — it has no second conversation to confuse itself
  with.
- **Every job event names its owner.** `chatStream` adopts a `job-started` only when it belongs to
  the chat this connection is for, and everything after adoption is already gated on that set — so a
  job never adopted can never render, step a row, or post a line in the wrong conversation. It fails
  **open** on an unstamped event: a deployment one release behind stamps nothing, and refusing every
  job line there would trade a rare wrong line for a permanent missing one.

The owning chat renders the job. Another chat shows nothing at all.

## The chat is free the moment the job starts

`busy` is the terminal's one in-flight flag. It is cleared on `job-started`, not on stream end, and
the send that started the job hands ownership of the flag over at that point (`ownsBusy`), so its
own `finally` cannot clear a flag a later turn now owns.

## The act does not wait for the turn in front of it

`Vexa-ai/vexa#1594`, founder walk 2026-09-06: *"extend this page button does not work when chat is
working"*. Two independent halves, and each one alone was enough to lose the press.

**The terminal dropped it.** `postIntent` → `ASK_CHAT_EVENT` → `Chat`'s `onAsk` → `send`, whose first
line returns on `state.busy`. No bubble, no row, no error, nothing on the wire, under a control that
looked exactly as it does when it works. Now an ask that arrives mid-turn **queues whole** — display,
prompt, intent, every option — and fires as its own turn the moment the current one ends; a page act
also puts a job row at the foot of the transcript **at once**, reading `queued behind the current
turn`, and that row is handed its job id on `job-started` rather than being replaced by a second one.
Never a silent drop, and never a disabled control.

**The worker read it late.** `serve()` is one thread and `run_message` holds it for the whole of a
chat turn, so a marked act that landed in `unit:<id>:in` behind an ordinary turn was not *read* until
that turn finished — which for the act that exists precisely to run beside the chat is the wait it was
built to remove. `_drain_jobs` now takes MARKED messages off the in-topic between the running turn's
output events: the job spawns then, its turn is acknowledged (nonce echoed, so the dispatcher's
warm-delivery watchdog is satisfied) and completed then, and the model that is mid-answer is not
touched at all.

Two rules the drain keeps, both about not owning what it is walking over:

- **It stops at the first entry it may not take.** The cursor is one position, so reaching past an
  ordinary message to grab an act behind it would consume the ordinary message too. Ordinary messages
  and `stop` are left where they are, in order, for the loop that owns them.
- **A marked act is never *injected*.** `_drain_inject` (mid-turn steering, `VEXA_MIDTURN_INJECT=1`)
  refuses one: injected, the mark would reach the running model as prose, the act would never spawn,
  and the person would read their own plumbing. Unlike injection the job drain is behind no flag —
  injection changes what the running turn is being told, a job touches nothing that turn owns.

With no `job` turn wired the drain does nothing at all and a marked prompt still runs inline, exactly
as before.

## Completion

`job-done` posts its line into the live chat as an agent turn, and the job's `commit` dispatches
`WORKSPACE_COMMIT_EVENT` — which is already what makes the pages panel re-read the open document
(`docNonce`) and what `useIntentLanding` waits on to front the page the act named. **The tab refresh
needed no new terminal plumbing**; it needed the job's commit not to be swallowed.

`job-failed` posts one line saying so. Never silence: a job that dies says it died.

## A job is not a turn

`openai_agent`'s loop carries a hard per-turn budget — max tool calls, max wall seconds — sized
against the CCC node (`llm/openai_agent.py`, SIZING). That sizing is about how much of the box ONE
request may hold at once, and says nothing about how many times a piece of work may come back for
another one. Jobs were billed as turns: the founder's OeNB job ran 72 steps and then died on the
40-call budget, with everything it had already written sitting on disk.

So a job gets its own budget, and reaching it is a **checkpoint rather than a death**:

| | turn | job |
|---|---|---|
| tool calls | `VEXA_AGENT_MAX_TOOL_CALLS` (40) | `VEXA_AGENT_JOB_MAX_TOOL_CALLS` (160), **per window** |
| wall clock | `VEXA_AGENT_MAX_TURN_SEC` (900) | `VEXA_AGENT_JOB_MAX_TURN_SEC` (3600), whole job |

Both job dials are **floored at the turn's**, so raising `VEXA_AGENT_MAX_TOOL_CALLS` on the
containers as a stopgap can never leave a job with less than a turn.

On reaching the call budget the loop emits `job-progress` — the job's row says how far it got and
that it is continuing — and opens a **fresh window**: the original brief, plus one sentence saying
that everything already written is on disk and to carry on from it rather than start over. Nothing
is checkpointed by this code because nothing needs to be: `run_harness_turn` commits the job's
writes as they land, which is what makes a fresh window able to simply read them.

It stops for two reasons and no others: **a window that made no tool call at all** (there is nothing
left it can do, and looping would be a spinner with a model bill), and **the whole-job clock**. Both
report exactly as they do today — `done.ok=false` with the reason on it, and the chat gets one line
saying the job failed.

The mark is a **thread-local** (`llm/jobs.in_job`), set by `worker/engine.serve`'s job turn on the
thread that iterates the harness. Not an env var: a job and its chat run in the same process at the
same time, so a process-wide flag would hand the chat turn the job's budget at random.

Only `openai-agent` reads it. `claude-code` and `codex` drive a vendor CLI with its own limits;
there is nothing here for them to honour.

## Refusal

One job per `target` (the workspace-qualified page path). A second act on the same page while one
runs is refused — `job-refused` with a reason — rather than queued. Two agents writing one file is
the failure `graph/sg/Operating-Loops.md` names in a line, and a queue here would mean the person
presses Extend twice and waits four minutes for an answer to the first press.

Different pages run concurrently; several jobs at once is the normal case.

## Jobs survive nothing

A job is a thread in a warm worker. A restart cancels it, and **the chat is told**: the runner writes
`<chat_root>/.claude/jobs/<job_id>.json` when a job starts and removes it when it ends, and `serve()`
scans that directory at boot — every file still there was killed by the restart, so it emits one
`job-failed` for it and deletes it. Nothing is resumed. `.claude/` is already outside the workspace
commit, so this leaves no trace in history.

Two consequences of "a thread in this worker":

- **Idle-reap waits.** The serve loop's empty `xread` returns the container to the reaper; with a job
  running it keeps serving instead, and every exit path joins the job threads first — the same
  discipline `_join_trailers` already applies to the write-back trailer.
- **The job shares the chat's harness instance** (one instance owns the warm worker's lifetime).
  Safe for `openai-agent` (every turn is local state over a shared `httpx.Client`) and for
  `claude-code` (every turn is its own subprocess). Not proven for `codex`, whose adapter holds one
  app-server process — a deployment running jobs on `codex` should give the job its own instance
  first.

## What a job does NOT share with its chat

`session_continuity=False`. The job runs its own harness session: it does not resume the
conversation, does not write the chat's continuity pointer, and does not append to the chat's
transcript. Two turns writing one transcript is the same one-writer failure as two turns writing one
page — and on `claude-code` a second `--resume` of a live session is not a supported shape.

The price is stated rather than hidden: **the acknowledgement line and the completion line are live
only.** They are in the event stream, not in the harness transcript `GET
/api/sessions/<s>/history` reads, so a reload does not show them. What IS durable is the thing that
matters — the page the job wrote and the workspace commit that carries it.

The other known interaction: a job's commit and a chat turn's commit can collide on `index.lock`.
`run_harness_turn` already treats a failed per-mount commit as "skip this mount", and `git add -A`
on the next turn picks the tree up — a commit lands one turn late, never a tree lost.

## Both runners

The job runner is above the harness, so **Create and Extend are non-blocking on every runner**
without either adapter knowing what a job is.

The `spawn_job` TOOL — the agent choosing to background its own long act — is attached on
`openai-agent` only:

- **`openai-agent`** implements its own tool loop in-process, so `spawn_job` is a builtin
  (`BUILTIN_SPECS`) whose execution is a request into `llm/jobs.py` — a register the worker installs
  a spawner into at boot. Accept or refuse is decided at tool-call time, in the same process, and the
  model reads the refusal as an ordinary failed tool result and can say so.
- **`claude-code`** drives a CLI subprocess whose tool list is the CLI's own; a Python builtin is
  unreachable from it. **The native subagent (`Task`) was considered and rejected**: it runs to
  completion INSIDE the turn, so the turn does not return early, there is no job id on our event
  channel and no completion line after the turn ends — it is the blocking behaviour with a different
  name, which is the one thing this contract exists to stop. Giving the CLI the same tool means
  putting the register behind a transport the subprocess can reach (a stdio MCP server shipped in the
  worker image is the obvious shape); that is v2, and until it lands `claude-code` has jobs but not
  the agent-initiated kind.

## Tests

`core/agent/tests/test_jobs.py` — a fake stream, a fake turn, and a real FastAPI app over fakes:
what the worker is actually told for a Create/Extend press, the spawn returning at once, progress
carrying the job id and never the turn id, completion and failure each posting one line, a second
job on the same target refused, a restart telling the chat, `spawn_job` reaching the same runner and
the same refusal, and the relay holding its view open for the job it watched start — plus, since
`#1613`, that a boot never reports another chat's job, still reports its own, cleans a pre-owner
record in silence, and that every event names its session.

`core/agent/tests/test_llm_openai_agent.py` — a job's own tool-call budget, the floor under it, a
window that ends becoming a fresh one over the same brief (and not a failure), a window that made no
progress ending the job, and a plain turn untouched by any of it.

`clients/terminal/src/surfaces/__tests__/jobs.test.ts` — the rendering half: the chip's line, the
job lane in `streamChatTurn` (past `turn-complete`, the shared `commit`, a foreign job ignored), the
failure line, **a job event from session A never rendering in chat B**, and a `job-progress` note
changing what the row says without changing what it counts.
