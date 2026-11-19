-- P7 · Second-invite rate by a non-organizer — THE HEADLINE. Datasource: vexa-flows-db.
--
-- The loop this deployment spreads on, stated exactly (PRD §16.2 item 5, Intra-Company-PLG
-- loop 2): a person who was an ATTENDEE on somebody else's invite, and who had never organized
-- one before that, LATER put the mailbox on a meeting of their own.
--
-- The cohort is everyone who was ever an attendee before they were ever an organizer. The
-- numerator is the share of that cohort who later organized. Both halves read `invite.received`
-- rows only — the fact that a person invited the mailbox is the invite arriving, exactly the
-- stage-3 test the adoption simulator uses (core/flows/eval/adoption/funnel.py:6).
--
-- ALL-TIME BY DESIGN, not windowed: conversion happens weeks after the attendance that seeded
-- it, so a trailing window on the cohort would drop the very people who converted. P9 gives the
-- rate its time shape; $window_days does not apply here.
--
-- READS BLANK, NEVER ZERO, when no invite carries an attendee roster: the cohort is empty, the
-- NULLIF makes the rate NULL, and Grafana shows "No data". That is the honest state — check P10
-- before reading this panel at all.
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
SELECT round(100.0 * count(*) FILTER (WHERE first_organized IS NOT NULL)
             / nullif(count(*), 0), 1) AS "Second-invite rate %"
FROM cohort;
