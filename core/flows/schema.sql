-- core/flows — production DDL (Postgres). Applied idempotently at boot.
-- Times are epoch seconds (double precision): one clock representation across dialects,
-- injected via the Clock port so tests advance time without sleeping.

CREATE TABLE IF NOT EXISTS reaction (
  reaction_id      text PRIMARY KEY,
  source_event_id  text NOT NULL UNIQUE,          -- dedup by constraint, never by memory
  event_type       text NOT NULL,
  subject_refs     text NOT NULL,                 -- JSON: opaque refs (meeting, owner, workspace)
  flow             text NOT NULL,
  flow_version     integer NOT NULL,
  step             text NOT NULL,
  status           text NOT NULL CHECK (status IN
    ('admitted','running','blocked','retrying','failed','cancelled','done')),
  attempt          integer NOT NULL DEFAULT 0,
  next_run_at      double precision NOT NULL,     -- time IS a column
  blocked_deadline double precision,
  lease_until      double precision,
  reason           text,
  scratch          text,                            -- durable per-reaction scratch (JSON): conversation bookkeeping survives worker restarts
  created_at       double precision NOT NULL,
  updated_at       double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS reaction_due
  ON reaction (next_run_at) WHERE status IN ('admitted','retrying');

CREATE TABLE IF NOT EXISTS effect_receipt (
  effect_key   text PRIMARY KEY,                  -- "{reaction_id}:{step}:{target}"
  reaction_id  text NOT NULL REFERENCES reaction (reaction_id),
  step         text NOT NULL,
  state        text NOT NULL CHECK (state IN ('reserved','confirmed','failed')),
  provider_ref text,
  result       text,                              -- JSON the next step consumes
  attempted_at double precision NOT NULL,
  confirmed_at double precision
);
CREATE INDEX IF NOT EXISTS receipt_by_reaction ON effect_receipt (reaction_id);

CREATE TABLE IF NOT EXISTS signal (
  signal_id   text PRIMARY KEY,
  reaction_id text NOT NULL REFERENCES reaction (reaction_id),
  kind        text NOT NULL CHECK (kind IN ('resume','retry','cancel')),
  actor       text NOT NULL,
  reason      text,
  created_at  double precision NOT NULL,
  consumed_at double precision
);

-- ── mail threading (the integration's state): outbound registers its Message-ID → session;
--    inbound In-Reply-To resolves to the conversation it belongs to (threaded, never sender-matched)
CREATE TABLE IF NOT EXISTS mail_thread (
  message_id  text PRIMARY KEY,
  subject_uid text NOT NULL,               -- platform user id the session belongs to
  session     text NOT NULL,               -- agent chat session (onboarding · meet-<id> · group-<slug>)
  created_at  double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_cursor (
  id   integer PRIMARY KEY CHECK (id = 1),
  uid  integer NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_outbox_sent (
  subject_uid text NOT NULL,
  session     text NOT NULL,
  hash        text NOT NULL,               -- one outbox content → ONE email, across ALL reactions
  sent_at     double precision NOT NULL,
  PRIMARY KEY (subject_uid, session, hash)
);
