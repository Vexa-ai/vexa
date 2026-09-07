# The adoption panel

A Grafana dashboard the customer runs **inside their own cluster**, against their own two
Postgres databases. Nothing leaves the perimeter, nothing phones home, and no component of this
directory is reachable from outside the namespace it is installed into.

It answers one question in two registers at once. For the platform team's champion it is the
impact case they take to their department head: *is this spreading inside the organisation
without the vendor in the room?* For us it is the consented usage report the per-active-user
meter bills on — the same artifact, which is the whole point of
[`Intra-Company-PLG`](https://github.com/DmitriyG228/biz/blob/main/graph/sg/Intra-Company-PLG.md)
§ *The alignment engine*: the champion's success metric and our invoice are the same number, so
we are not selling against them.

## What is here

| File | What it is |
|---|---|
| `sql/*.sql` | **The source of truth for every query.** One file per panel, with the definition stated in its header comment. |
| `gen_dashboard.py` | Generates `adoption-panel.json` from `sql/` plus the layout declared in the script. `--check` fails on drift. |
| `adoption-panel.json` | **Generated — do not hand-edit.** The Grafana dashboard. |
| `datasource-template.yaml` | A Grafana datasource provisioning file for the two databases, with credentials left as environment placeholders. |

## What the chart does and does not do

The chart ships the dashboard and the datasource template as two ConfigMaps, behind
`adoptionPanel.enabled` (**off by default**). It does **not** deploy Grafana. Standing a
Grafana into a central bank's cluster is that bank's platform decision, not a side effect of
installing a meeting bot — and the delivery kit already carries a declared hole
(`kube-prometheus-stack-not-mirrored`) about shipping monitoring CRDs it does not mirror.

Both ConfigMaps carry the labels the Grafana sidecar watches (`grafana_dashboard: "1"` /
`grafana_datasource: "1"`), so an operator who already runs Grafana with the sidecar gets the
dashboard automatically. An operator who does not can lift the JSON out and import it by hand:

```bash
kubectl -n <ns> get cm <release>-vexa-adoption-dashboard \
  -o jsonpath='{.data.adoption-panel\.json}' > adoption-panel.json
```

## Why SQL panels and not Prometheus counters

Because **there is no exporter to reuse, and the metrics are not counters.**

Checked on the line before deciding: the repository contains no Grafana, no Prometheus, no
`ServiceMonitor`, no `prometheus_client`, and no `/metrics` route on any service — zero hits,
repo-wide. So "reuse the existing exporter" was never an option; the choice was between
*building* a metrics pipeline and *querying the rows we already have*.

Every number on this dashboard is a **business fact about identifiable people over an unbounded
history** — *has this person, ever, organized a meeting after first being an attendee?* A
Prometheus counter cannot answer that at any retention: it stores a scalar per label set per
scrape, and the cardinality of "one series per person" is exactly what you must not do to a
Prometheus. The second-invite rate needs a self-join across a person's whole history. That is a
relational query, and the rows are already durable in Postgres because the flows engine needs
them to be.

The rule this follows: an exporter would be a **second home for a fact that already has one**.
If a service later grows a genuine `/metrics` surface for operational health — request rates,
queue depth, bot liveness — that is a different dashboard with a different reader, and it should
not absorb these panels.

## Datasources

Two, because there are two databases and Postgres cannot join across them.

| uid | Database | Tables read |
|---|---|---|
| `vexa-app-db` | `database.name` (default `vexa`) | `users`, `meetings` |
| `vexa-flows-db` | `flows.databaseName` (default `flows`) | `reaction` |

**No panel joins across the two**, by construction — every panel names exactly one datasource.
When a deployment sets `flows.databaseName == database.name` (the shape compose runs) both
datasources point at the same database and nothing about the dashboard changes.

Grant the Grafana role `SELECT` and nothing else. The dashboard issues no writes.

## The panels

`window` = the panel honours the `$window_days` variable (default 30).
`threshold` = it also honours `$min_meetings` (default 1).

| # | Panel | Datasource | Defined as | Knobs |
|---|---|---|---|---|
| 1 | **Second-invite rate by a non-organizer** | flows | Of everyone who was an attendee on somebody else's `invite.received` **before** they had ever been an organizer on one, the share who **later** appear as the `organizer` of an `invite.received` of their own. All-time, not windowed. | — |
| 2 | **Active users** | flows | Distinct people present in ≥ `$min_meetings` **captured** meetings inside the window. Present = `refs.organizer` **or** a member of `refs.participants`; captured = the meeting reached `meeting.completed`. **Provisional — see below.** | window · threshold |
| 3 | **Meetings captured** | flows | Distinct `refs.meeting_id` on `meeting.completed` reactions inside the window. | window |
| 4 | **Teams covered** | flows | Distinct non-empty `refs.group` — the `#group:<name>` tag in the invite — on captured meetings inside the window. | window |
| 5 | **Invites carrying an attendee roster** | flows | Share of `invite.received` in the window whose `refs.participants` is a non-empty array. **Not an adoption metric — the trustworthiness gauge for 1, 2 and 8.** | window |
| 6 | **Second invites per week** | flows | Panel 1's numerator on a time axis: people whose first-ever organized invite fell in that ISO week, having previously only ever attended. | — |
| 7 | **Distinct people in captured meetings, per week** | flows | Distinct people present in a captured meeting in each ISO week. Deliberately **not** called active users — no window, no threshold. | — |
| 8 | **Second-invite cohort** | flows | Panel 1 as four numbers: cohort size · how many converted · the rate · median days from first attendance to first organized invite. | — |
| 9 | **Coverage by team** | flows | Per `#group:` tag inside the window: meetings captured and distinct people. Untagged meetings appear as one explicit `(no team tag)` row rather than being dropped. | window |
| 10 | **Active users by calendar month** | flows | Panel 2's definition bucketed by calendar month instead of a trailing window. **This is the table that becomes the usage report.** Counts of people only — never addresses. | threshold |
| 11 | **Accounts on the platform** | app | `count(*)` over `users`, and how many were created inside the window. Not the same as panel 2 — the gap between provisioned and active is the thing worth seeing. | window |
| 12 | **Meetings completed (platform cross-check)** | app | `count(*)` over `meetings` with `status = 'completed'` inside the window. An **independent** count of panel 3 from the other database. | window |

### "Active user" is PROVISIONAL — a founder open item, not a settled definition

Panels 2 and 10 implement it as *organized or attended at least `$min_meetings` captured
meetings in the trailing `$window_days`*, defaulting to **1 meeting / 30 days**. Both halves are
dashboard variables precisely because the definition is not settled: it is listed open in the
PRD (§16.5, *"The 'active user' definition (founder)"*) and in
[`Grow-Vision`](https://github.com/DmitriyG228/biz/blob/main/graph/sg/Grow-Vision.md), which
notes that monthly-versus-weekly is worth seven figures at 40,000 staff and must be settled
**before** any bank-band negotiation.

Until a human settles it, this dashboard shows a defensible default a customer can argue with —
and the fact that it moves when you move a slider is the honest signal, not a defect. Do not
quote panel 10 as an invoice.

### The second-invite panels read blank, not zero, when the input is missing

Panels 1, 6 and 8 are computed entirely from `refs.participants`. Two situations leave that
absent:

- an invite parsed before the ICS `ATTENDEE` lines were extracted, and
- a `meeting.completed` published by meeting-api rather than by the invite flow — its domain
  holds no invite, so it carries no roster, and it can win the admission race
  (`core/flows/contracts/flows.v1/carriers.json`).

Every query guards with `jsonb_typeof(... ) = 'array'`, so an absent roster degrades to
organizer-only rather than erroring — but the resulting cohort is empty and the rate is `NULL`,
which Grafana renders as **No data**. *No data* and *0%* mean completely different things here:
the first says we cannot see, the second says nobody forwarded an invite. **Panel 5 is what
tells them apart, and it is on the top row for that reason.**

### Two things these queries assume

- **`reaction.subject_refs` is valid JSON.** It is `TEXT`, not `JSONB` — the engine writes
  `json.dumps(...)` into it — so every panel casts. A row containing non-JSON would fail the
  panel rather than skip the row; Postgres has no `try_cast`.
- **The scans are bounded by volume, not by an index.** `subject_refs` is an opaque blob with
  nothing to push a predicate into — the same constraint the flows engine itself works under.
  At a central bank's meeting volume this is a table scan of thousands of rows per refresh,
  which is fine; at millions it would want a materialised read model, and that is the moment to
  build one rather than now.

### Calendar buckets are pinned to UTC, and that is not cosmetic

Panels 6, 7 and 10 bucket by ISO week or calendar month with `AT TIME ZONE 'UTC'` before
truncating. Without it, `date_trunc` on a `timestamptz` resolves in the **session's** timezone,
which Grafana sets from the dashboard's `timezone: browser` — so the month a person was counted
in would depend on where the reader was sitting. It was found by rendering the dashboard in two
browsers and seeing two different months off one database (`receipt/README.md`), and it is now
asserted across UTC+14, UTC and UTC−11.

### It adds database connections the chart's budget does not know about

`gate:db-budget` accounts for every service *this chart deploys*; Grafana is not one of them.
The datasource template caps each connection pool at `maxOpenConns: 4`, so an operator running
both datasources adds **up to 8 connections** on top of the chart's declared ceiling. Today that
fits inside the reserved headroom (the budget reports `Σ 70/100`, with 10 reserved), but it is
close enough that an operator scaling replicas should look at both numbers together rather than
either alone.

## Changing a query

Edit the `.sql` file, then regenerate and re-check:

```bash
python3 deploy/helm/charts/vexa/dashboards/gen_dashboard.py
python3 deploy/helm/charts/vexa/dashboards/gen_dashboard.py --check
```

`core/identity/services/admin-api/tests/test_stack_adoption_panel.py` runs every query against
an ephemeral Postgres seeded with a known adoption story and asserts the answers, and separately
asserts the committed JSON still matches `sql/`. A hand-edit of the JSON fails that test.
