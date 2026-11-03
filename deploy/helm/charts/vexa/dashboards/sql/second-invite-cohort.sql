-- P8 · Second-invite cohort — the four numbers behind P7. Datasource: vexa-flows-db.
--
-- Same cohort as P7, shown as counts rather than one percentage, because a rate on a cohort of
-- three is not a finding. The median is in DAYS: `first_organized`/`first_attended` are the
-- reaction table's own clock — epoch SECONDS in a DOUBLE PRECISION column — so the difference
-- divides by 86400.
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
SELECT
  count(*)::bigint AS "attendee-only cohort",
  count(*) FILTER (WHERE first_organized IS NOT NULL)::bigint AS "later organized",
  round(100.0 * count(*) FILTER (WHERE first_organized IS NOT NULL)
        / nullif(count(*), 0), 1) AS "rate %",
  (SELECT round((percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (first_organized - first_attended)) / 86400.0)::numeric, 1)
   FROM cohort WHERE first_organized IS NOT NULL) AS "median days to second invite"
FROM cohort;
