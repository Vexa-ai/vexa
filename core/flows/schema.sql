-- GENERATED from src/flows_schema/models.py (the SSOT) by core/flows/scripts/gen_schema.py.
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

CREATE TABLE IF NOT EXISTS mail_send (
	id TEXT NOT NULL, 
	at DOUBLE PRECISION NOT NULL, 
	kind TEXT NOT NULL, 
	template_version TEXT NOT NULL, 
	recipient_domain TEXT NOT NULL, 
	result TEXT NOT NULL, 
	detail TEXT, 
	message_id TEXT, 
	test INTEGER NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS mail_send_kind_at ON mail_send (kind, at);

CREATE TABLE IF NOT EXISTS mail_test_send (
	kind TEXT NOT NULL, 
	template_version TEXT NOT NULL, 
	at DOUBLE PRECISION NOT NULL, 
	by TEXT NOT NULL, 
	PRIMARY KEY (kind, template_version)
);

CREATE TABLE IF NOT EXISTS mail_outbox_sent (
	subject_uid TEXT NOT NULL, 
	session TEXT NOT NULL, 
	hash TEXT NOT NULL, 
	sent_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (subject_uid, session, hash)
);

CREATE TABLE IF NOT EXISTS flow_version (
	scope TEXT DEFAULT 'instance' NOT NULL, 
	name TEXT NOT NULL, 
	version INTEGER NOT NULL, 
	on_event TEXT NOT NULL, 
	cron TEXT, 
	steps TEXT NOT NULL, 
	params TEXT, 
	status TEXT NOT NULL, 
	created_by TEXT, 
	created_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (scope, name, version), 
	CONSTRAINT flow_status CHECK (status IN ('draft','active','retired'))
);

CREATE TABLE IF NOT EXISTS friction_occurrence (
	dedup_key TEXT NOT NULL, 
	friction_id TEXT NOT NULL, 
	uid TEXT NOT NULL, 
	occurrences INTEGER NOT NULL, 
	first_seen DOUBLE PRECISION NOT NULL, 
	last_seen DOUBLE PRECISION NOT NULL, 
	admitted INTEGER NOT NULL, 
	PRIMARY KEY (dedup_key)
);

CREATE INDEX IF NOT EXISTS friction_occurrence_by_id ON friction_occurrence (friction_id);

CREATE TABLE IF NOT EXISTS keyed_schedule (
	scope TEXT NOT NULL, 
	flow TEXT NOT NULL, 
	key TEXT NOT NULL, 
	fire_at DOUBLE PRECISION NOT NULL, 
	status TEXT NOT NULL, 
	version INTEGER NOT NULL, 
	event_type TEXT NOT NULL, 
	person TEXT NOT NULL, 
	payload TEXT, 
	fired_for DOUBLE PRECISION, 
	updated_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (scope, flow, key), 
	CONSTRAINT keyed_status CHECK (status IN ('armed','fired','cancelled','missed'))
);

CREATE INDEX IF NOT EXISTS keyed_due ON keyed_schedule (fire_at);

CREATE INDEX IF NOT EXISTS keyed_person ON keyed_schedule (scope, person);

CREATE TABLE IF NOT EXISTS sensor (
	scope TEXT NOT NULL, 
	name TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	connection_id TEXT, 
	filter TEXT, 
	interval_s DOUBLE PRECISION NOT NULL, 
	version INTEGER NOT NULL, 
	tested_version INTEGER, 
	tested_at DOUBLE PRECISION, 
	cursor TEXT, 
	status TEXT NOT NULL, 
	mode TEXT NOT NULL, 
	next_due_at DOUBLE PRECISION NOT NULL, 
	failures INTEGER NOT NULL, 
	reason TEXT, 
	last_tick_at DOUBLE PRECISION, 
	last_change_at DOUBLE PRECISION, 
	last_events TEXT, 
	lease_until DOUBLE PRECISION, 
	created_by TEXT, 
	created_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (scope, name), 
	CONSTRAINT sensor_status CHECK (status IN ('draft','active','degraded','stopped'))
);

CREATE INDEX IF NOT EXISTS sensor_due ON sensor (next_due_at);

CREATE TABLE IF NOT EXISTS delivery (
	person TEXT NOT NULL, 
	ref TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	org TEXT NOT NULL, 
	workspace TEXT NOT NULL, 
	room TEXT NOT NULL, 
	owner TEXT NOT NULL, 
	names TEXT, 
	link TEXT NOT NULL, 
	stage TEXT NOT NULL, 
	wake_at DOUBLE PRECISION NOT NULL, 
	notice_id TEXT, 
	outcome TEXT, 
	attempts INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	armed_at DOUBLE PRECISION NOT NULL, 
	notified_at DOUBLE PRECISION, 
	emailed_at DOUBLE PRECISION, 
	digested_at DOUBLE PRECISION, 
	updated_at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (person, ref), 
	CONSTRAINT delivery_stage CHECK (stage IN ('armed','notified','digest','done'))
);

CREATE INDEX IF NOT EXISTS delivery_due ON delivery (stage, wake_at);

CREATE INDEX IF NOT EXISTS delivery_person ON delivery (person, stage);

CREATE TABLE IF NOT EXISTS delivery_send (
	id TEXT NOT NULL, 
	person TEXT NOT NULL, 
	day TEXT NOT NULL, 
	channel TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	refs TEXT NOT NULL, 
	status TEXT NOT NULL, 
	handle TEXT, 
	reason TEXT, 
	at DOUBLE PRECISION NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT delivery_send_status CHECK (status IN ('sending','sent','failed')), 
	CONSTRAINT delivery_send_kind CHECK (kind IN ('single','digest'))
);

CREATE INDEX IF NOT EXISTS delivery_send_day ON delivery_send (person, day);

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
