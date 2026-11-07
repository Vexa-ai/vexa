-- P2 · Distinct people in captured meetings, per week. Datasource: vexa-flows-db.
--
-- NAMED FOR WHAT IT COMPUTES, deliberately. It is not "active users over time": active carries
-- a trailing window and a threshold ($window_days, $min_meetings), and a rolling re-evaluation
-- of that per week is a different and much heavier query. This is the raw reach curve — how
-- many distinct people a captured meeting touched in each ISO week — and P1 is the thresholded
-- number the meter reads. Do not quote this one as an active-user count.
--
-- CALENDAR BUCKETS ARE PINNED TO UTC (`AT TIME ZONE 'UTC'`), and that is load-bearing rather
-- than tidy: `to_timestamp` yields a timestamptz, `date_trunc` on a timestamptz resolves in the
-- SESSION's timezone, and Grafana sets that from the dashboard's `timezone: browser`. Without
-- the pin the same database showed 2026-09 to one reader and 2026-08 to another — i.e. which
-- bucket a person landed in depended on where the reader was sitting. Caught by rendering it;
-- one test session cannot see it.
WITH completed AS (
  SELECT r.subject_refs::jsonb AS refs,
         date_trunc('week', to_timestamp(r.created_at) AT TIME ZONE 'UTC') AS wk
  FROM reaction r
  WHERE r.event_type = 'meeting.completed'
),
present AS (
  SELECT wk, lower(btrim(refs->>'organizer')) AS email
  FROM completed
  WHERE btrim(coalesce(refs->>'organizer', '')) <> ''
  UNION
  SELECT wk, lower(btrim(p))
  FROM completed
  CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(refs->'participants') = 'array'
         THEN refs->'participants' ELSE '[]'::jsonb END) AS p
  WHERE btrim(p) <> ''
)
SELECT wk AS "time",
       count(DISTINCT email)::bigint AS "people in captured meetings"
FROM present
GROUP BY wk
ORDER BY wk;
