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
| `job-done` | the job's thread | `job_id`, `kind`, `target`, `line`, `ok: true` |
| `job-failed` | the job's thread | `job_id`, `kind`, `target`, `line` |

and **every event the job's own turn yields is tagged `{**ev, "job_id": …}` and carries no
`turn_id`.** That is the whole of "progress reaches the terminal tagged with a job id": a job's
`tool-call`s are the job's step count, and a consumer that keys on `turn_id` cannot mistake them for
the chat turn's.

The turn that spawns a job emits, in order: `turn-accepted` · `message-delta` (the one short line) ·
`job-started` · `turn-complete`. It runs no model call at all — the acknowledgement is composed by
the worker, not asked for.

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

## The chat is free the moment the job starts

`busy` is the terminal's one in-flight flag. It is cleared on `job-started`, not on stream end, and
the send that started the job hands ownership of the flag over at that point (`ownsBusy`), so its
own `finally` cannot clear a flag a later turn now owns.

## Completion

`job-done` posts its line into the live chat as an agent turn, and the job's `commit` dispatches
`WORKSPACE_COMMIT_EVENT` — which is already what makes the pages panel re-read the open document
(`docNonce`) and what `useIntentLanding` waits on to front the page the act named. **The tab refresh
needed no new terminal plumbing**; it needed the job's commit not to be swallowed.

`job-failed` posts one line saying so. Never silence: a job that dies says it died.

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
the same refusal, and the relay holding its view open for the job it watched start.

`clients/terminal/src/surfaces/__tests__/jobs.test.ts` — the rendering half: the chip's line, the
job lane in `streamChatTurn` (past `turn-complete`, the shared `commit`, a foreign job ignored), and
the failure line.
