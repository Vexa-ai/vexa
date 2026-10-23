# Receipt — the adoption panel was actually rendered

Evidence that the dashboard in `deploy/helm/charts/vexa/dashboards/` loads in a real Grafana
against a real Postgres and produces the numbers its tests claim. Kept because "the SQL passes
pytest" and "a human opened the dashboard and saw the right thing" are different claims, and
only the second one is what a customer does.

| File | What it shows |
|---|---|
| `adoption-panel-rendered.png` | All 12 panels, live, on the seeded story. **Second-invite rate 50.0%** — hand-checkable against the story below. |
| `adoption-panel-degraded.png` | The same dashboard after `refs.participants` was stripped from every row. The second-invite panels read **No data**, roster coverage reads **0.0%** in red, active users falls back to organizer-only. This is the honest-degradation claim, rendered. |
| `seed_rig.py` | Seeds the rig with the same story the pytest asserts on. |
| `dashboards-provider.yaml` | The file-based dashboard provider the throwaway Grafana needs. In a cluster this is the sidecar reading the ConfigMap by label; here it is a mount. |

## The seeded story

    alice organizes m1, m2 and invites bob + carol
    bob, having only ever attended, later organizes m3 of his own   -> THE SECOND INVITE
    carol attends and never organizes                               -> in the cohort, not converted
    dave organizes m4 from his very first row                       -> never in the cohort at all
    m5 arrives with no attendee roster                              -> 4 of 5 invites carry one = 80%

    cohort = {bob, carol} · converted = {bob} · second-invite rate = 1/2 = 50.0%

## What rendering it caught that the tests did not

The month bucket moved between two viewers — `2026-09` in one browser, `2026-08` in another,
off the same database. `to_timestamp()` returns a `timestamptz`, `date_trunc` on a `timestamptz`
resolves in the **session's** timezone, and Grafana sets that from the dashboard's
`timezone: browser`. So the month a person was billed in depended on where the reader was
sitting — on the one table that becomes an invoice.

Confirmed directly against the rig, unpinned versus pinned, same nine rows:

```
unpinned UTC                : 2026-08:9
unpinned Pacific/Kiritimati : 2026-08:8  2026-09:1     <- the same data, split across two months
unpinned Pacific/Midway     : 2026-08:9
pinned   (AT TIME ZONE 'UTC') — identical in all three zones
```

Every calendar bucket is now pinned with `AT TIME ZONE 'UTC'`, and
`test_calendar_buckets_do_not_move_with_the_readers_timezone` asserts it across UTC+14, UTC and
UTC−11 so it cannot come back. A single-session test could never have seen this.

## Reproducing it

Docker on `bbb`, never on the laptop. Nothing here touches `vexa-dogfood`.

```bash
docker network create adoption-panel-net
docker run -d --name ap-pg --network adoption-panel-net -p 33456:5432 \
  -e POSTGRES_PASSWORD=panel -e POSTGRES_USER=panel -e POSTGRES_DB=vexa postgres:16-alpine
docker exec ap-pg psql -U panel -d vexa -c "CREATE DATABASE flows"

cd core/identity/services/admin-api && uv run python ../../../../receipt/seed_rig.py \
  "$(git rev-parse --show-toplevel)" \
  postgresql://panel:panel@127.0.0.1:33456/vexa \
  postgresql://panel:panel@127.0.0.1:33456/flows

D=deploy/helm/charts/vexa/dashboards
docker run -d --name ap-grafana --network adoption-panel-net -p 33900:3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin \
  -e GF_AUTH_DISABLE_LOGIN_FORM=true -e GF_USERS_DEFAULT_THEME=light \
  -e VEXA_DB_HOST=ap-pg -e VEXA_DB_PORT=5432 -e VEXA_APP_DB=vexa -e VEXA_FLOWS_DB=flows \
  -e VEXA_DB_USER=panel -e VEXA_DB_PASSWORD=panel -e VEXA_DB_SSLMODE=disable \
  -v "$PWD/$D/datasource-template.yaml":/etc/grafana/provisioning/datasources/vexa.yaml:ro \
  -v "$PWD/receipt/dashboards-provider.yaml":/etc/grafana/provisioning/dashboards/vexa.yaml:ro \
  -v "$PWD/$D/adoption-panel.json":/var/lib/grafana/dashboards/adoption-panel.json:ro \
  grafana/grafana:11.3.0

google-chrome --headless=new --no-sandbox --window-size=1600,1200 --virtual-time-budget=45000 \
  --screenshot=receipt/adoption-panel-rendered.png \
  "http://localhost:33900/d/vexa-adoption-panel/vexa-e28094-adoption?from=now-90d&to=now&kiosk"

docker rm -f ap-grafana ap-pg && docker network rm adoption-panel-net
```

**Note that the rig proved one thing the chart does NOT ship:** the datasource file was mounted
straight from the repository with its `${VAR}` placeholders and Grafana expanded them from the
container's environment. That is the same file the chart puts in a ConfigMap — so the
placeholder shape is proven, not assumed.

## Still unproven

- **No sidecar was exercised.** The ConfigMaps carry `grafana_dashboard: "1"` /
  `grafana_datasource: "1"`, which is the documented contract of the standard Grafana sidecar,
  but this rig mounted files directly instead of running a sidecar against a cluster. The label
  contract is asserted statically in `gate:helm`, never end-to-end.
- **No air-gapped Grafana.** `grafana/grafana:11.3.0` was pulled from Docker Hub. Whether the
  customer's mirrored channel carries a Grafana image at all is an open delivery question — the
  kit's own receipts already declare `kube-prometheus-stack-not-mirrored` as a hole.
- **Volume is untested.** The rig holds nine reaction rows. These queries scan `reaction`
  without an index and cast `subject_refs` per row; that is fine at a pilot's volume and
  unmeasured beyond it.
