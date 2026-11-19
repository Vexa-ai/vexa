- **An external Postgres without a superuser can run the session store and the CRM.** The
  `agent-sessions-schema` and `crm-schema` Jobs used to fail on any database user that was not a
  superuser, even one granted `CREATEROLE`. They now work with `CREATEROLE`, or with roles your
  database team creates, and when a privilege is missing they stop before writing anything and name
  the statement to run. See
  [Database privileges on an external Postgres](/deployment-kubernetes#database-privileges-on-an-external-postgres).
