-- P6 · Teams covered. Datasource: vexa-flows-db.
--
-- A "team" is the `#group:<name>` tag the organizer put in the invite — the only place in the
-- whole substrate where a meeting declares which team it belongs to. THIS IS A LIMIT, not a
-- measurement: a deployment that does not use the group convention reads 0 here forever while
-- being fully adopted. Read it next to P11, which shows the untagged share explicitly.
SELECT count(DISTINCT lower(btrim(r.subject_refs::jsonb->>'group')))::bigint AS "Teams covered"
FROM reaction r
WHERE r.event_type = 'meeting.completed'
  AND btrim(coalesce(r.subject_refs::jsonb->>'group', '')) <> ''
  AND to_timestamp(r.created_at) >= now() - make_interval(days => ('$window_days')::int);
