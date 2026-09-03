-- P3 · Meetings captured (flows) — distinct meetings that reached `meeting.completed` inside
-- the trailing $window_days. Datasource: vexa-flows-db.
--
-- Counted on the meeting id rather than on rows: a completion can be admitted by more than one
-- producer and the reaction table dedups on the source event id, not on the meeting.
SELECT count(DISTINCT r.subject_refs::jsonb->>'meeting_id')::bigint AS "Meetings captured"
FROM reaction r
WHERE r.event_type = 'meeting.completed'
  AND to_timestamp(r.created_at) >= now() - make_interval(days => ('$window_days')::int);
