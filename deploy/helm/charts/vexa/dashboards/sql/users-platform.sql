-- P5 · Accounts on the platform. Datasource: vexa-app-db.
--
-- NOT the same number as P1, and the difference is the point: an account exists the moment
-- somebody is provisioned; an ACTIVE user is somebody a captured meeting actually touched. The
-- invoice reads P1. This panel is here so the gap between "provisioned" and "active" is visible
-- rather than assumed.
SELECT
  count(*)::bigint AS "Accounts",
  count(*) FILTER (
    WHERE created_at >= (now() AT TIME ZONE 'utc')
                        - make_interval(days => ('$window_days')::int))::bigint
    AS "New in window"
FROM users;
