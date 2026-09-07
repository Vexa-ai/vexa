-- P10 · Invite roster coverage — READ THIS BEFORE P7. Datasource: vexa-flows-db.
--
-- The share of invites in the window that arrived carrying a non-empty attendee roster. It is
-- not an adoption metric; it is the trustworthiness gauge for every attendee-derived number on
-- this dashboard. At 0% the second-invite panels are blank because the input is missing, NOT
-- because nobody forwarded an invite — and those two states look identical without this panel.
SELECT round(100.0 * count(*) FILTER (
         WHERE jsonb_typeof(r.subject_refs::jsonb->'participants') = 'array'
           AND jsonb_array_length(r.subject_refs::jsonb->'participants') > 0)
       / nullif(count(*), 0), 1) AS "Invites carrying an attendee roster %"
FROM reaction r
WHERE r.event_type = 'invite.received'
  AND to_timestamp(r.created_at) >= now() - make_interval(days => ('$window_days')::int);
