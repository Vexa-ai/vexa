# ADR 0048 — Sessions are conversations, stored apart from workspaces

**Status:** accepted · 2026-10-12 · founder · [#1783](https://github.com/Vexa-ai/vexa/issues/1783)
· decision 5 is a default the founder may strike; decision 6 is the founder's (2026-10-12) ·
builds on ADR-0038 (S3 everywhere) and ADR-0046 (flows absorb routines) · applies P3, P11, P18,
P20 and P23

## Context

A chat's history lives on a workspace volume. Each harness writes its transcript under the
continuity root of the turn (`_system` when mounted, otherwise the turn's workspace):

- claude-code: `<root>/.claude/projects/<cwd-slug>/<sid>.jsonl`, reached through a
  `~/.claude/projects` link (`core/agent/llm/claude_code.py` `_link_chat_into_workspace`);
- openai-agent: the same shape, written by the adapter itself (`llm/openai_agent.py`);
- codex: `<root>/.claude/codex/sessions/…` (`llm/codex.py` `_link_sessions_into_workspace`);
- the worker: the resume pointer `.claude/sessions/<session>.session` and the person's own words
  `.claude/sessions/<session>.turns.jsonl` (`worker/engine.py`).

agent-api reads them back from the same tree (`control_plane/workspace_reader.py` `history`). The
folder is gitignored and hidden from every file view, and the rail is a per-person Redis index
(`api_shared._Sessions`).

So a conversation is anchored to one person's workspace. It cannot be shared without sharing that
workspace, a second person's turn cannot continue it, and a routine thread (ADR-0046 decision 12) or
an agent-to-agent message (decision 13) addresses something whose storage belongs to a desk. The
founder's direction: sessions are stored on their own, so a chat can gain people and agents and
behave like a channel, and a call becomes one more thing a session can hold.

## Decision

1. **A session is owned by the agent module, not by a workspace.** It has an id, an owner,
   participants (people and agents) with roles (`owner`, `member`, `viewer`), a title, the
   workspaces it uses, an optional flow binding (a routine thread, ADR-0046 decision 12), and an
   optional attachment to a workspace — a *channel*: visible to and joinable by that workspace's
   members.

2. **Storage.** Session metadata lives in the `agent_session*` tables of the instance's Postgres,
   written only by agent-api and reached as the store's **own role** (`vexa_agent_sessions` by
   default): a login with no admin power and grants on those tables only, created with the admin
   credential by the migrations path (`control_plane/session_schema.py init` — compose
   `agent-sessions-init`, the Helm `agent-sessions-schema` Job, the Lite entrypoint). agent-api never
   holds the admin credential and runs no DDL against Postgres: it refuses a superuser login and any
   schema version but its own, and the store stays off. Transcripts — the harness transcript files
   and the turn records — live in object storage (S3, which every install has since ADR-0038)
   through boto3, one immutable object per turn version, in the store's **own bucket**
   (`vexa-sessions`) with a key scoped to that bucket alone. storage-init makes the bucket and the
   key's scope on the bundled storage and proves the key is refused on the recordings bucket; Helm
   names an operator-made bucket and key (the chart makes no bucket, so the store is off there until
   one is named). The recordings bucket and a storage root key are refused as the store's. There is
   no directory store. Workspaces hold knowledge only. With the store on a worker keeps no conversation under a mount:
   a turn that carries none (a routine's run now carries its thread's) keeps its harness state in a
   private folder outside every mount, and the agent reads other chats only with `chats_search` and
   `chat_read`.

3. **The worker contract.** At turn start agent-api hands the worker the session's current
   transcript bundle; the worker materialises it into a private directory outside every mount and
   runs the harness with that directory as its continuity root. At turn end the worker returns the
   bundle and agent-api saves it as the next version, only if the version it was based on is still
   the head. A conflict is refused and logged, never merged silently. The same contract holds for
   claude-code, codex and openai-agent, because all three already keep their state under the
   continuity root.
   - **One turn at a time.** Before dispatch agent-api leases the session's head to one unit (in
     Redis). A turn another unit asks for while the lease is held is refused with a typed 409
     `session_conflict`; nothing runs.
   - **A refused save is visible.** If the store still refuses a turn's version, the refusal is
     recorded, and `GET /api/sessions/{id}/history` returns a `session_conflict` fault until the
     next save succeeds. The reply is never dropped silently. In a session with one person, a hand-back on an older base
     that carries every stored file further (append-only transcripts) is saved onto the head instead.
   - A hand-back is saved only for the session and base version that unit was handed, and only if
     the speaker can still write.

4. **Two gates, and the leak rule.**
   - *Session access* — participants and roles — decides who reads and writes the conversation.
   - *Workspace access* — the speaking person's own membership, narrowed by the job's ceiling —
     decides what the agent may read and write in that turn.
   - Sharing a session never grants a workspace. A participant's turn runs without the workspaces
     that participant cannot reach.
   - **What a newcomer sees is the owner's choice** *(founder, 2026-10-13: "share this chat" means
     the chat, with its history)*. When the owner shares a session only they are in, they choose
     `history`: `all` (the person joins it in place, history included), `from:<turn>` (a new
     session carrying the visible text from that turn on), or `none` (a new empty session). Named
     nothing, it is `all`. Naming anything sensitive in the history is the agent's job in chat: one
     line before it shares, then it proceeds on the owner's yes. The person added is notified ("chat
     shared with you"). Anyone else adding a person starts a new empty session of their own:
     the history is not theirs to give. The leak rule below governs what the agent may *use* in
     shared turns, from the moment the second person joins; it is not a limit on what the owner
     chooses to show.
   - **Leak rule.** An answer posted into a session is read by every participant. So a turn in a
     session with more than one person may use only the workspaces that *every current
     participant* can read (the intersection; CRM records join it with ADR-0047, planned), unless
     the speaker explicitly shares a specific item for that turn. Private material stays in private chats. When a turn needs
     something outside the intersection the agent says it cannot use it here and offers to answer
     in the speaker's private chat.
   - **The tools obey the leak rule too, not only the files.** A turn in a session with other
     people is minted *shared*: the delegation carries `scope.shared`, the gateway signs it as
     `delegation.shared` / `x-user-delegation-shared`, and a refresh keeps it. Its worker is refused
     every person-wide read, with reason `shared_conversation`: agent-api refuses its connections
     routes (Gmail, Calendar, any other connection) and meeting routes; the credential broker refuses
     every agent-role verb, listing included, so no connection token or secret reaches it;
     meeting-api refuses every route (meetings, transcripts, recordings, bots); flows-api refuses
     every route (the person's flows, reactions, timeline, queue) but filing a friction report. The only explicit
     per-turn share is a workspace page, which agent-api reads with the speaker's own access and
     puts into the prompt; no grant comes with it.

5. **When the agent speaks** *(default; the founder may strike)*. In a one-person session, on every
   message. With two or more people, only when mentioned (`@vexa` or an agent's name) or asked
   directly. An admin changes the default for the whole deployment with the policy key
   `session_agent_speaks` (`always` | `mentioned`) in `_global/POLICIES.md`; there is no
   per-workspace setting.

6. **Calls** *(founder, 2026-10-12: LiveKit — "building own [calls] is wrong … port an open source
   service provider")*. A session can start an ad-hoc call on a self-hosted LiveKit server
   (Apache-2.0), an optional deployable module; audio first (Opus with redundancy, DTX, adaptive
   jitter; in-browser noise suppression with RNNoise, BSD), video available through the same server.
   We operate a media server; we do not build one. The call is a meeting attached to the session and
   is transcribed by default, with a visible "this call is transcribed" notice to every participant.
   Capture needs no browser bot: a server-side listener subscribes to each participant's audio track
   and feeds the same buffer and transcription pipeline voice notes use, one channel per speaker, so
   attribution comes from the track rather than from guessing. Jitsi stays a platform the bot joins,
   as today.
   - **The call's shared chat is the session.** Everyone in the call and the agent are in it; the
     agent reads the live transcript and answers when mentioned (decision 5).
   - **A private side chat.** Each participant can open, at the same time, a private chat bound to
     the same meeting — their own agent, their own workspaces, the live transcript — invisible to
     everyone else. *Share to the room* posts one chosen message from it into the shared chat;
     nothing crosses automatically.

   - **A meeting started from a chat is that chat's** *(founder, 2026-10-13, the routine rule)*. A
     bot sent from a chat (by the model, or by the express lane while a turn runs) and a voice note
     recorded from it bind the meeting to that session; a call is the session's by construction.
     The chat's header shows the bound meeting — state, Stop bot, its transcript page — the meeting
     is the turn context there when a turn names no other focus, and anything addressed to the
     meeting's own name (`meet-<row>`: the post-meeting notes and proposals of decision 7, history,
     the queue) resolves to the bound chat, per person; the meeting page's **Chat** opens it. A
     meeting started outside any chat (calendar auto-join, the API, an admin) keeps a chat of its
     own. Several meetings from one chat are each bound; the header shows the first one, and
     showing whichever is live is *planned*. Meeting chats made before this stay as they are.

7. **After a call or a meeting** the post-meeting flow produces notes and *proposed* changes
   (workspace pages, entity and CRM fields, commitments). The meeting owner — who started the call
   or owns the meeting — accepts, edits or rejects each. Nothing lands in a shared workspace until
   accepted, and an accepted change is committed attributed to the owner. Policy may allow
   auto-accept per workspace or per change kind; the default is off.

8. **Everything else addresses sessions**: mentions, requests, agent-to-agent messages
   (ADR-0046 decision 13), and the session's trace and audit.
   - **Agents read the person's own chats** *(founder, 2026-10-13)*: `chats_search` and `chat_read`
     (agent-api `/api/chats/…`, through the gateway) return the person's words and the agent's
     replies from the turn records of conversations the speaker can read (gate 1). In a shared turn
     only conversations every current participant can read, else `shared_conversation`; a run with
     nobody in the loop reads only its owner's own conversations; every read is logged as
     `chat_read` with who, which conversation, how many turns and the session it came from.
   - **Meeting links and bot commands never wait.** While a turn runs, a meeting link or a bot command
     at the head of the queue is carried out beside it, with one line of its own; a bot sent so binds
     to that chat like any other. A queued message can be sent now, which stops the running turn.
   - **People are added by @-mention** *(founder, 2026-10-13: "adding people to conversation should
     be with @ symbol in chat instead")*. The composer offers only the people the speaker shares a
     group workspace with, and a mention carries the person's account id. On Enter, a mention of
     someone not in the session asks once whether to share it: in place with its history, or as a
     new chat with them (decision 4). The header only shows who is in it. The agent can make
     the same adds with `chat_participant_add` and grant a workspace with `workspace_member_add`,
     only with a person in the loop and only as an owner.
   - **A routine's session is shared the same way.** The flow stays its owner's; the shared session
     takes the flow binding, and its runs are shared turns under decision 4.

9. **Gap list**, built after the foundation: unread state, reactions, edit and delete, pins,
   presence and typing, mobile push, one search across messages, retention and legal hold, guests
   and cross-organisation participants.
   - **Notifications — built** *(founder, 2026-10-13: "we need notifications popup in UI when people
     invite each other etc, and if a chat session is shared it should appear in their chats as
     shared")*. One store: a table in the session store's database, `agent_session_notification`,
     one row per recipient (id, kind, actor, object, text, created, read). Not the flows timeline:
     that is another domain's read-only history with no per-person read state, and every kind here
     is an act an agent-api route performs, so agent-api is the one writer — through the store's own
     role, inside its `agent_session*` family. One writer per kind: the route that does the act
     emits it (adding a person to a chat, an @-mention, a routine's run, binding a call, a workspace
     invite or add, accepting or declining an invite). The leak rule holds by construction: the
     sentence is composed from the actor's and the object's names only. Delivery is the recipient's
     own channel `u:{user_id}:notifications`, which the gateway's `/ws` fans in for each of their
     sockets; on load, unread rows come from the store. A chat somebody else owns lands in a built-in
     per-person **Shared with me** section of the rail, and leaving drops it. Unread *messages* inside
     a chat remain on the list.

10. **Migration.** Existing `.claude` transcripts are copied into the store — idempotent, verified
    by reading back, logging counts only. Workspaces are cleaned only after verification. For one
    release the old layout is still read as a fallback.

## Relation to other decisions

- **ADR-0046.** A routine thread is a session bound to a flow (decision 1's flow binding); its
  `flow-<digest>` id is a session id, and `message_agent(thread, …)` addresses a session. That ADR's
  "no second conversation store" holds: this store *is* the conversation store, and the routine
  thread uses it like every other session.
- **ADR-0047 (planned: groups, CRM).** Groups become a kind of participant and a workspace's channel
  membership; CRM records join workspaces in the leak rule's intersection (decision 4).

## Build order

Foundation (this change): the session store (A); the worker materialise and save-back for all three
harnesses, with the old layout as fallback (B); migration of existing transcripts (C); participants
and roles, the two gates, the leak rule and the when-the-agent-speaks rule (D); the terminal's chat
list from the store, with adding and removing participants (E). Calls (6), the private side chat
and owner review (7) are built in a separate change on top of this foundation.

## Alternatives

- **Keep transcripts in the workspace and share the workspace.** Rejected: sharing a conversation
  would hand over a desk, and a second person's turn still could not continue it.
- **Build our own call layer, or Jitsi as the call layer.** Rejected for calls (founder, 2026-10-12): media is a service to operate, not to write; Jitsi would keep a browser bot in the capture path, LiveKit gives per-participant tracks to a server-side listener.
- **Transcripts in the database.** Rejected: transcripts are large, append-shaped and per-turn
  versioned; object storage is the store every install already has for that shape.
- **Merge concurrent turns.** Rejected: two harness transcripts cannot be merged meaningfully. One
  head per session and a refused conflict is the honest answer.

## Consequences

- agent-api gains a database role and an object-store key of its own — neither the instance's admin
  credential nor the recordings key — and becomes the one writer of every conversation. A worker
  never sees either; the bundle travels on its own unit's input and output.
- A transcript leaves the workspace volume, so a workspace export or share no longer carries
  anybody's chats.
- A shared session's agent knows less than either person's private chat (the leak rule). That is
  the price of an answer every participant can read.
- Two turns cannot both continue the same session; the second is refused and sent again.
