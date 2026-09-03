-- P9 · Second invites per week — the spread curve. Datasource: vexa-flows-db.
--
-- Each point is the number of people who, in that ISO week, organized their FIRST invite having
-- previously only ever been an attendee. This is P7's numerator given a time axis; it is the
-- line the champion shows a department head, because it goes up without us in the room.
--
-- CALENDAR BUCKETS ARE PINNED TO UTC (`AT TIME ZONE 'UTC'`), and that is load-bearing rather
-- than tidy: `to_timestamp` yields a timestamptz, `date_trunc` on a timestamptz resolves in the
-- SESSION's timezone, and Grafana sets that from the dashboard's `timezone: browser`. Without
-- the pin the same database showed 2026-09 to one reader and 2026-08 to another — i.e. which
-- bucket a person landed in depended on where the reader was sitting. Caught by rendering it;
-- one test session cannot see it.
WITH inv AS (
  SELECT r.subject_refs::jsonb AS refs, r.created_at AS ts
  FROM reaction r
  WHERE r.event_type = 'invite.received'
),
organized AS (
  SELECT lower(btrim(refs->>'organizer')) AS email, min(ts) AS first_organized
  FROM inv
  WHERE btrim(coalesce(refs->>'organizer', '')) <> ''
  GROUP BY 1
),
attended AS (
  SELECT lower(btrim(p)) AS email, min(i.ts) AS first_attended
  FROM inv i
  CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(i.refs->'participants') = 'array'
         THEN i.refs->'participants' ELSE '[]'::jsonb END) AS p
  WHERE btrim(p) <> ''
  GROUP BY 1
),
cohort AS (
  SELECT a.email, a.first_attended, o.first_organized
  FROM attended a
  LEFT JOIN organized o ON o.email = a.email
  WHERE o.first_organized IS NULL OR o.first_organized > a.first_attended
)
SELECT date_trunc('week', to_timestamp(first_organized) AT TIME ZONE 'UTC') AS "time",
       count(*)::bigint AS "second invites"
FROM cohort
WHERE first_organized IS NOT NULL
GROUP BY 1
ORDER BY 1;
