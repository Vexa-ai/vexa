-- P4 · Meetings completed (platform) — the INDEPENDENT cross-check on P3.
-- Datasource: vexa-app-db (the application database).
--
-- Two counts of the same thing from two databases that do not talk to each other. They will not
-- match exactly — the platform counts every bot dispatch that completed, flows counts only the
-- ones a flow reacted to — and the GAP is the finding: a large one means completions are not
-- reaching flows, which silently flattens every adoption number on this dashboard.
--
-- `meetings.created_at` is a naive UTC timestamp (server_default now() on a UTC server), so the
-- window is computed in UTC rather than in the session's zone.
SELECT count(*)::bigint AS "Meetings completed (platform)"
FROM meetings
WHERE status = 'completed'
  AND created_at >= (now() AT TIME ZONE 'utc') - make_interval(days => ('$window_days')::int);
