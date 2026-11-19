-- THE ADOPTION PANEL'S DATABASE ROLE — SELECT on the columns the panel reads, and nothing else.
--
-- Run once by the database owner, then put the role's name and password in the Secret
-- `adoptionPanel.readerSecret` names (keys `username`, `password`). Grafana logs in as this role and
-- never as the application's owner role: a Grafana Editor can run any SQL the datasource's role may,
-- so the role is the boundary, not Grafana's `readOnly` lock.
--
--   psql "$OWNER_DSN_APP"   -v password="$READER_PASSWORD" -f reporting-role.sql   # creates the role
--   psql "$OWNER_DSN_APP"   -f reporting-role.sql -v app=1                         # app grants
--   psql "$OWNER_DSN_FLOWS" -f reporting-role.sql -v flows=1                       # flows grants
--
-- Column grants, not table grants: `users.data` carries people's model keys and calendar feed URLs,
-- and `reaction.scratch` carries step state; the panel reads neither.

\if :{?password}
CREATE ROLE vexa_reporting LOGIN PASSWORD :'password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT CONNECTION LIMIT 8;
\endif

\if :{?app}
GRANT CONNECT ON DATABASE :"DBNAME" TO vexa_reporting;
GRANT USAGE ON SCHEMA public TO vexa_reporting;
GRANT SELECT (created_at) ON public.users TO vexa_reporting;
GRANT SELECT (status, created_at) ON public.meetings TO vexa_reporting;
\endif

\if :{?flows}
GRANT CONNECT ON DATABASE :"DBNAME" TO vexa_reporting;
GRANT USAGE ON SCHEMA public TO vexa_reporting;
GRANT SELECT (created_at, event_type, subject_refs) ON public.reaction TO vexa_reporting;
\endif
