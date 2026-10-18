-- P11 · Coverage by team. Datasource: vexa-flows-db.
--
-- Meetings and distinct people per `#group:` tag inside the window. Untagged meetings are NOT
-- dropped — they land in one explicit "(no team tag)" row, so the reader can see how much of
-- the estate the team breakdown is actually describing.
WITH completed AS (
  SELECT r.subject_refs::jsonb AS refs
  FROM reaction r
  WHERE r.event_type = 'meeting.completed'
    AND to_timestamp(r.created_at) >= now() - make_interval(days => ('$window_days')::int)
),
roster AS (
  SELECT coalesce(nullif(lower(btrim(refs->>'group')), ''), '(no team tag)') AS team,
         refs->>'meeting_id' AS meeting_id,
         lower(btrim(refs->>'organizer')) AS email
  FROM completed
  WHERE btrim(coalesce(refs->>'organizer', '')) <> ''
  UNION
  SELECT coalesce(nullif(lower(btrim(refs->>'group')), ''), '(no team tag)'),
         refs->>'meeting_id',
         lower(btrim(p))
  FROM completed
  CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(refs->'participants') = 'array'
         THEN refs->'participants' ELSE '[]'::jsonb END) AS p
  WHERE btrim(p) <> ''
)
SELECT team AS "team",
       count(DISTINCT meeting_id)::bigint AS "meetings captured",
       count(DISTINCT email)::bigint AS "people"
FROM roster
GROUP BY team
ORDER BY 2 DESC, 1;
