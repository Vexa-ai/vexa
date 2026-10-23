-- P1 · Active users (PROVISIONAL definition — see dashboards/README.md, and PRD §16.5).
-- Datasource: vexa-flows-db (the flows database).
--
-- A person is ACTIVE when, inside the trailing $window_days, they were present in at least
-- $min_meetings meetings that were actually CAPTURED. "Present" is organizer OR attendee;
-- "captured" is `meeting.completed`, which is only admitted once the bot ran, so a meeting the
-- bot never joined can never make anyone active.
--
-- DEGRADES, never errors: an invite whose ATTENDEE lines were never parsed (or a completion
-- published by meeting-api, whose domain holds no invite — core/flows/contracts/flows.v1/
-- carriers.json) carries no `participants`, and the jsonb_typeof guard makes that an empty
-- roster rather than a failed query. The reading is then organizer-only, and P10 tells you by
-- how much.
WITH completed AS (
  SELECT r.subject_refs::jsonb AS refs
  FROM reaction r
  WHERE r.event_type = 'meeting.completed'
    AND to_timestamp(r.created_at) >= now() - make_interval(days => ('$window_days')::int)
),
present AS (
  SELECT lower(btrim(refs->>'organizer')) AS email,
         refs->>'meeting_id' AS meeting_id
  FROM completed
  WHERE btrim(coalesce(refs->>'organizer', '')) <> ''
  UNION
  SELECT lower(btrim(p)) AS email,
         refs->>'meeting_id' AS meeting_id
  FROM completed
  CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(refs->'participants') = 'array'
         THEN refs->'participants' ELSE '[]'::jsonb END) AS p
  WHERE btrim(p) <> ''
)
SELECT count(*)::bigint AS "Active users"
FROM (
  SELECT email
  FROM present
  WHERE meeting_id IS NOT NULL
  GROUP BY email
  HAVING count(DISTINCT meeting_id) >= ('$min_meetings')::int
) t;
