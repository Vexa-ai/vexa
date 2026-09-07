## v0.12.27

### Workflows — a new domain
- **Durable product workflows, as two tables and a loop (#1456, @DmitriyG228).** `flows-api`,
  `flows-worker` and the optional `flows-mailbox` — one image, three commands — turn a fact from the
  outside world into exactly one reaction row, run one step, record a receipt and advance. Ten
  Postgres tables in the stack's own database; no message broker, no scheduler, no second state
  store. Dedup is a `UNIQUE` constraint, "every effect exactly once" is a receipt, and every wait is
  a timestamp column. Documented at [Workflows](https://docs.vexa.ai/flows/overview).
- **Flows are data: compose one without a rebuild (#1456, @DmitriyG228).** `POST /flows` takes a
  name, a trigger and an ordered list of step names, validated against the deployed vocabulary at
  submission and live in the worker in about ten seconds. The API never accepts code — steps are
  reviewed Python in the image.
- **The mailbox decides who it will act for before it admits anything (#1456, @DmitriyG228).** An
  empty allow-list means the mailbox's own domain, not everyone; anybody else gets no account, no
  agent turn and no model call — one `mail_quarantine` row, readable with one `SELECT`.
- **Tests, fixtures and the flows documentation (#1497, @DmitriyG228).**

### Agents and MCP
- **`whats_waiting` — what your person's Vexa needs right now (#1532, #1545, @DmitriyG228).** An
  agent asks it at the start of a session and gets a queue scoped to its own caller, each item
  carrying the sentence to say and the typed reason behind it. A person with no meeting yet gets a
  first step instead of an empty answer. The words come from files an admin edits, with no deploy on
  either side of the edit.
- **Standing notices ride along with unrelated work (#1546, #1551, @DmitriyG228).** An item whose
  copy declares itself a standing notice travels on the meeting tools' own results — and on their
  refusals — so an agent hears it without going looking. `GET /queue/notices` asks for just those
  sentences and nothing else.
- **Friction reporting (#1532, @DmitriyG228).** `report_friction` files what did not work; no field
  is required and **no value a caller can send is refused** — an unknown word, an over-long value or
  a missing session are all filed rather than rejected, because a report we cannot tie back to a
  conversation is worth strictly more than no report. `friction_so_far` reads your own back.
- **A refused tool call reaches the agent as structure (#1546, @DmitriyG228).** `reason`, `message`,
  `action_url` and the upstream body as fields, instead of prose with JSON inside it.
- **A refusal carries the deciding service's own words (#1550, @DmitriyG228).** Services return
  `message` and `action_url` with the reason; the API and the terminal render them verbatim rather
  than mapping a reason onto copy of their own. A deployment's refusals now say what that deployment
  means.
- **The MCP edge advertises a vocabulary; it never enforces one (@DmitriyG228).** A word the edge
  did not expect is passed to the owning route rather than rejected at the door.

### Meetings
- **Full-text search over your own transcripts (#1456, @DmitriyG228).** `GET /transcripts/search`.
  The index builds itself out of band on first boot, without locking the table.
- **Annotate a meeting, then find it by what you wrote (#1547, @DmitriyG228).** `POST
  /meetings/{id}/annotate` attaches a title and arbitrary metadata during or after a meeting;
  `GET /meetings?metadata=` filters on it in the database (16 KB and 64 keys per meeting).
- **Address a meeting by its row id (#1547, @DmitriyG228).** `/meetings/{id}/…` and
  `POST /meetings/{id}/share` beside the existing platform/native pair.
- **`GET /recordings` is a page, newest first (#1547, @DmitriyG228).** `limit` and `offset` on a list
  shape.
- **Zoom served under a hosted or vanity hostname is recognised (#1547, @DmitriyG228).**

### Security
- **The gateway strips authority headers by family, not by name (#1456, @DmitriyG228).** At 0.12.26
  the strip was an eight-name list of `x-user-*` spellings, so a public client could send
  `x-internal-secret` — the value published in `docker-compose.yml` — and be believed by the internal
  tier. Any header beginning `x-user-`, `x-internal-` or `x-vexa-internal-`, plus `x-admin-api-key`
  and `x-gateway-verified`, is now dropped from every client request.
- **`POST /bots` no longer hands back the webhook signing secret it just stored (#1547,
  @DmitriyG228).** The response was a verbatim copy of the stored row, so the secret travelled
  through the public gateway into caller logs and agent context. Rotate any webhook secret minted by
  an earlier version.
- **Boot refuses the published placeholder secrets (#1456, @DmitriyG228).** See *Upgrade notes*.
- **agent-api validates the workspace repository host and pins git transports (#1539,
  @DmitriyG228).**

### Deployment
- **The agent surface is unchanged (#1553, @DmitriyG228).** `/agent/*` keeps the seven routes it
  served at 0.12.26, now declared in `core/agent/routes.v1.json` like every other domain, with the
  compose default restored. A deployment that leaves `AGENT_API_URL` unset serves no agent surface
  and answers `404` there — at 0.12.26 the same state answered `403`.
- **Dependency floors across the Python, pnpm and transcript-rendering lockfiles (#1541, #1542,
  #1544, @DmitriyG228).**
- **The ASWF 2020 v2 CCLA is accepted as an alternative corporate instrument (#1378,
  @DmitriyG228).** A company whose legal has already approved that shape does not need a bespoke
  one.
- **Carve manifest for the v0.12.26 train (#1418, @DmitriyG228).**

### Fixed
- A person's settings move to identity, so one answer serves every service (#1456, @DmitriyG228) —
  see *Upgrade notes*.
- One absence is said once, however many meetings ran into it (#1547, @DmitriyG228) — a deployment
  that does not run the agent domain no longer adds an identical queue item per completed meeting.
- The friction sink never loses a report to a vocabulary word, and never to a missing one
  (@DmitriyG228).
- `GET /reactions` answered `500` for every authenticated caller: the route shadowed the helper it
  called (@DmitriyG228).
- `/.well-known/mcp-tools.json` was served offline and absent live — the module's entrypoint guard
  sat above the route, so nothing below it ran in the process every deployment starts (@DmitriyG228).

> **Known limit — three things this release does not finish.** The **person-settings import is
> operator-triggered**: settings move from the workspace file to identity, and an existing
> deployment that does not run the one-shot migration starts everyone on the defaults (mail on,
> clock in UTC) rather than on what they had. The **agent-half flow tests are skipped when the agent
> module is absent**, which is the shape OSS ships — those flows (`meeting_prep`, `email_chat`, the
> desk pair) are not registered at all without an agent domain, so their suites prove nothing here
> and are marked present-only. And **Python dependency licences are still unasserted**: the licence
> gate resolves the npm tree, `pip-licenses` scanning is owed rather than done (ADR-0009 §
> *Python licence scanning*), and the per-release SPDX carries those fields as `NOASSERTION`
> alongside the Ubuntu packages the Lite final stage installs. The gate is green on an inventory
> that does not actually assert those terms.

## Upgrade notes (breaking)

Read these before upgrading a self-hosted deployment. Each one changes behaviour that a 0.12.26
deployment relies on.

1. **Generate a real `INTERNAL_API_SECRET`.** Boot now refuses the literals this repo has published
   (`vexa-internal-secret`, `lite-internal-secret`, `changeme`, …) and stops naming the variable. Use
   `openssl rand -hex 32` and set the same value on every service that talks to another. Vexa Lite
   mints a random one per boot and needs nothing set. `VEXA_FLOWS_API_KEY` has no default at all and
   refuses the same placeholders — flows-api will not start without it.
2. **Upgrade the gateway with, or before, the services behind it.** The authority-header strip is now
   a prefix family rule. Anything that was reaching an internal tier by sending its own
   `x-internal-*` header through the public edge stops working — which is the point.
3. **MCP tool errors changed shape.** A refusal arrives as `reason` / `message` / `action_url` /
   body, not as one sentence. Anything scraping the old text breaks. `notices` is a new key on five
   tools' results.
4. **`/agent/*` answers `404`, not `403`, when `AGENT_API_URL` is unset.**
5. **Run the person-settings import.** Timezone and the mail switches are read from admin-api, not
   from `.settings.json` in the workspace. Without the operator-triggered migration everyone reverts
   to defaults — anyone who had mail off starts receiving it again, in UTC.
6. **`FLOWS_API_URL` → `VEXA_FLOWS_API_URL`** on admin-api. The old name is honoured for one release;
   the new one wins when both are set.
7. **`X-Flows-Admin-Key` → `X-Flows-Operator-Key`.** Accepted for one release, with a warning once
   per process. The old name read as admin-api's token, which it never was.
8. **`PROC_PENDING_GRACE_SEC` is removed.** No longer read; delete it from your `.env`.
9. **Copilot processed notes are no longer persisted after the bot stops.** The durable notes pane
   and the schedule digest's `notes` flag stay empty once a meeting ends.
10. **Rotate any webhook signing secret minted before this release** — earlier `POST /bots` responses
    returned it in the clear.
11. **Email lookups on the admin API are case-insensitive.** Reconcile existing case-variant
    duplicate accounts before upgrading; which one resolves is otherwise plan-dependent.
12. **`flows` expects a database that has never held its tables.** The engine creates them with
    `CREATE … IF NOT EXISTS` and ships no migration runner.

---

**Images (eleven — `vexaai/v012-flows` joins the release set with this version):** <images>, published from the reviewed candidate packet
`releases/v0.12.27/candidate-images.json` and validated by <validation run>.

**In production:** this release is what vexa.ai runs, pinned as channel entry <channel entry> and
re-verified against the cluster after the pin.
