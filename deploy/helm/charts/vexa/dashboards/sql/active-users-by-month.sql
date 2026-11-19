-- P12 · Active users by calendar month — THE CONSENTED USAGE REPORT.
-- Datasource: vexa-flows-db.
--
-- The same definition as P1, bucketed by calendar month instead of a trailing window, because
-- an invoice is issued per month and a trailing-30-day number is not a month. This table IS the
-- artifact the customer exports and sends: it carries counts of people, never their addresses.
--
-- The definition it counts is PROVISIONAL and founder-open (PRD §16.5). Monthly-versus-weekly
-- is worth seven figures at 40,000 staff (Grow-Vision § depth beats breadth) — so this table is
-- a proposal in a shape a human can argue with, not a settled bill.
--
-- CALENDAR BUCKETS ARE PINNED TO UTC (`AT TIME ZONE 'UTC'`), and that is load-bearing rather
-- than tidy: `to_timestamp` yields a timestamptz, `date_trunc` on a timestamptz resolves in the
-- SESSION's timezone, and Grafana sets that from the dashboard's `timezone: browser`. Without
-- the pin the same database showed 2026-09 to one reader and 2026-08 to another — i.e. which
-- bucket a person landed in depended on where the reader was sitting. Caught by rendering it;
-- one test session cannot see it.
WITH completed AS (
  SELECT r.subject_refs::jsonb AS refs,
         date_trunc('month', to_timestamp(r.created_at) AT TIME ZONE 'UTC') AS mon
  FROM reaction r
  WHERE r.event_type = 'meeting.completed'
),
present AS (
  SELECT mon, lower(btrim(refs->>'organizer')) AS email, refs->>'meeting_id' AS meeting_id
  FROM completed
  WHERE btrim(coalesce(refs->>'organizer', '')) <> ''
  UNION
  SELECT mon, lower(btrim(p)), refs->>'meeting_id'
  FROM completed
  CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(refs->'participants') = 'array'
         THEN refs->'participants' ELSE '[]'::jsonb END) AS p
  WHERE btrim(p) <> ''
)
SELECT to_char(mon, 'YYYY-MM') AS "month",
       count(*)::bigint AS "active users"
FROM (
  SELECT mon, email
  FROM present
  WHERE meeting_id IS NOT NULL
  GROUP BY mon, email
  HAVING count(DISTINCT meeting_id) >= ('$min_meetings')::int
) t
GROUP BY mon
ORDER BY mon DESC;
