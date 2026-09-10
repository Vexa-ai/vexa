"""Every SQL statement the migrate tool can issue, as a named constant.

The tool executes these strings and nothing else. `docs/docs/upgrade-migrate-tool.mdx` quotes
them verbatim; a change here is a change to that page.

Read-only statements are prefixed `Q_`. Write statements are prefixed `W_` and are reachable
only from `migrate run --fix`.
"""
from __future__ import annotations

INDEX_NAME = "uq_meeting_active_user_platform_native"

#: Terminal statuses. The dedup index's partial predicate excludes exactly these, so a retired
#: duplicate must land on one of them or it stays inside the index and the build still fails.
TERMINAL_STATUSES = ("completed", "failed")


# --------------------------------------------------------------------------- #
# Q_ — read-only
# --------------------------------------------------------------------------- #

#: MIGRATION-0002 step 1 pre-flight. `platform_specific_id IS NOT NULL` because a unique index
#: treats NULLs as DISTINCT — rows with a NULL native id never collide and must not be touched.
Q_ACTIVE_DUPLICATE_GROUPS = """
SELECT user_id, platform, platform_specific_id,
       count(*) AS active_dups,
       array_agg(id ORDER BY created_at DESC, id DESC) AS meeting_ids
FROM meetings
WHERE status NOT IN ('completed', 'failed')
  AND platform_specific_id IS NOT NULL
GROUP BY user_id, platform, platform_specific_id
HAVING count(*) > 1
ORDER BY active_dups DESC
"""

#: Keep/retire plan: every row in every duplicate group, ranked. `rn = 1` is the keeper; every
#: `rn > 1` is retired. `:keep_ids` is the explicit `--keep-meeting-id` override (an empty array
#: when unused, which makes the first ORDER term false for every row and a no-op).
#: `{order}` is substituted from `ORDER_BY` below — it is one of two fixed strings, never
#: caller-supplied text.
Q_DEDUP_PLAN = """
WITH ranked AS (
  SELECT id, user_id, platform, platform_specific_id, status, bot_container_id, created_at,
         row_number() OVER (
           PARTITION BY user_id, platform, platform_specific_id
           ORDER BY {order}
         ) AS rn
  FROM meetings
  WHERE status NOT IN ('completed', 'failed')
    AND platform_specific_id IS NOT NULL
)
SELECT id, user_id, platform, platform_specific_id, status, bot_container_id, created_at, rn
FROM ranked
WHERE (user_id, platform, platform_specific_id) IN (
        SELECT user_id, platform, platform_specific_id
        FROM ranked
        GROUP BY user_id, platform, platform_specific_id
        HAVING count(*) > 1
      )
ORDER BY user_id, platform, platform_specific_id, rn
"""

#: The two orderings `--keep-strategy` selects between, each prefixed by the explicit-keep override.
ORDER_BY = {
    "newest": "(id = ANY(:keep_ids)) DESC, created_at DESC, id DESC",
    "live-bot": "(id = ANY(:keep_ids)) DESC, (bot_container_id IS NOT NULL) DESC, created_at DESC, id DESC",
}

#: Index state. `indisvalid = false` is the corpse a failed CONCURRENTLY build leaves behind.
Q_INDEX_STATE = """
SELECT i.indisvalid, i.indisunique, i.indisready, pg_get_indexdef(i.indexrelid) AS indexdef
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = :index_name
  AND n.nspname = current_schema()
"""

#: MIGRATION-0003 pre-flight (report only — the tool never writes this column).
Q_MAX_CONCURRENT_BOTS = """
SELECT max_concurrent_bots AS current_limit, count(*) AS users
FROM users
GROUP BY max_concurrent_bots
ORDER BY current_limit
"""

Q_MAX_CONCURRENT_BOTS_DEFAULT = """
SELECT column_default
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name = 'users'
  AND column_name = 'max_concurrent_bots'
"""

#: MIGRATION-0004 (report only — `ensure_schema` applies this one itself on the next boot).
Q_EMPTY_SCOPE_TOKENS = """
SELECT count(*) AS empty_scope_tokens
FROM api_tokens
WHERE scopes = '{}'::text[] OR scopes IS NULL
"""

Q_READ_ONLY_TXN = "SET TRANSACTION READ ONLY"


# --------------------------------------------------------------------------- #
# W_ — writes. Reachable only under `run --fix`.
# --------------------------------------------------------------------------- #

#: Retire the losing rows to a terminal status and stamp `data.dedup` so every touched row stays
#: queryable afterwards (`WHERE data ? 'dedup'`). Ids come from Q_DEDUP_PLAN, never recomputed
#: inside the UPDATE — the receipt lists the exact set before it is written.
W_RETIRE_DUPLICATES = """
UPDATE meetings m
SET status = :retire_status,
    data   = jsonb_set(coalesce(m.data, '{}'::jsonb), '{dedup}', (:stamp)::jsonb)
WHERE m.id = ANY(:loser_ids)
RETURNING m.id, m.user_id, m.platform, m.platform_specific_id, m.status
"""

#: MIGRATION-0002 step 3. CONCURRENTLY cannot run inside a transaction block — the tool issues
#: this on an AUTOCOMMIT connection.
W_CREATE_INDEX_CONCURRENTLY = (
    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}\n"
    "ON meetings (user_id, platform, platform_specific_id)\n"
    "WHERE status NOT IN ('completed', 'failed')"
)

#: Only ever issued against an index this tool has just observed as INVALID.
W_DROP_INDEX_CONCURRENTLY = f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"
