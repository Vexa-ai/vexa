-- GENERATED from src/flows/schema_models.py (the SSOT) by scripts/gen_schema.py.
-- DO NOT EDIT BY HAND — edit the models and regenerate. The engine and the sqlite
-- test rig consume this file so they stay stdlib-pure; the drift gate keeps it honest.

CREATE TABLE IF NOT EXISTS reaction (
	reaction_id TEXT NOT NULL, 
	source_event_id TEXT NOT NULL, 
	event_type TEXT NOT NULL, 
	subject_refs TEXT NOT NULL, 
	flow TEXT NOT NULL, 
	flow_version INTEGER NOT NULL, 
	step TEXT NOT NULL, 
	status TEXT NOT NULL, 
	attempt INTEGER NOT NULL, 
	next_run_at DOUBLE PRECISION NOT NULL, 
	blocked_deadline DOUBLE PRECISION, 
	lease_until DOUBLE PRECISION, 
	reason TEXT, 
	scratch TEXT, 
	created_at DOUBLE PRECISION NOT NULL, 
	updated_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (reaction_id), 
	CONSTRAINT reaction_status CHECK (status IN ('admitted','running','blocked','retrying','failed','cancelled','done')), 
	UNIQUE (source_event_id)
);

CREATE INDEX IF NOT EXISTS reaction_due ON reaction (next_run_at) WHERE status IN ('admitted','retrying');

CREATE TABLE IF NOT EXISTS mail_thread (
	message_id TEXT NOT NULL, 
	subject_uid TEXT NOT NULL, 
	session TEXT NOT NULL, 
	created_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (message_id)
);

CREATE TABLE IF NOT EXISTS mail_cursor (
	id SERIAL NOT NULL, 
	uid INTEGER NOT NULL, 
	token TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT cursor_singleton CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS mail_seen (
	source TEXT NOT NULL, 
	ext_id TEXT NOT NULL, 
	created TEXT NOT NULL, 
	seen_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (source, ext_id)
);

CREATE INDEX IF NOT EXISTS mail_seen_window ON mail_seen (source, created);

CREATE TABLE IF NOT EXISTS mail_quarantine (
	ext_id TEXT NOT NULL, 
	from_addr TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	facts TEXT, 
	at DOUBLE PRECISION NOT NULL, 
	replied_at DOUBLE PRECISION, 
	PRIMARY KEY (ext_id)
);

CREATE INDEX IF NOT EXISTS mail_quarantine_sender ON mail_quarantine (from_addr);

CREATE TABLE IF NOT EXISTS mail_turn (
	ext_id TEXT NOT NULL, 
	from_addr TEXT NOT NULL, 
	at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (ext_id)
);

CREATE INDEX IF NOT EXISTS mail_turn_window ON mail_turn (at);

CREATE TABLE IF NOT EXISTS mail_outbox_sent (
	subject_uid TEXT NOT NULL, 
	session TEXT NOT NULL, 
	hash TEXT NOT NULL, 
	sent_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (subject_uid, session, hash)
);

CREATE TABLE IF NOT EXISTS flow_version (
	name TEXT NOT NULL, 
	version INTEGER NOT NULL, 
	on_event TEXT NOT NULL, 
	steps TEXT NOT NULL, 
	params TEXT, 
	status TEXT NOT NULL, 
	created_by TEXT, 
	created_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (name, version), 
	CONSTRAINT flow_status CHECK (status IN ('draft','active','retired'))
);

CREATE TABLE IF NOT EXISTS effect_receipt (
	effect_key TEXT NOT NULL, 
	reaction_id TEXT NOT NULL, 
	step TEXT NOT NULL, 
	state TEXT NOT NULL, 
	provider_ref TEXT, 
	result TEXT, 
	attempted_at DOUBLE PRECISION NOT NULL, 
	confirmed_at DOUBLE PRECISION, 
	PRIMARY KEY (effect_key), 
	CONSTRAINT receipt_state CHECK (state IN ('reserved','confirmed','failed')), 
	FOREIGN KEY(reaction_id) REFERENCES reaction (reaction_id)
);

CREATE INDEX IF NOT EXISTS receipt_by_reaction ON effect_receipt (reaction_id);

CREATE TABLE IF NOT EXISTS signal (
	signal_id TEXT NOT NULL, 
	reaction_id TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	actor TEXT NOT NULL, 
	reason TEXT, 
	created_at DOUBLE PRECISION NOT NULL, 
	consumed_at DOUBLE PRECISION, 
	PRIMARY KEY (signal_id), 
	CONSTRAINT signal_kind CHECK (kind IN ('resume','retry','cancel','wake')), 
	FOREIGN KEY(reaction_id) REFERENCES reaction (reaction_id)
);
