-- Frozen initial MCAD schema. Add future changes as a new migration.

CREATE TABLE import_batches (
	id VARCHAR(36) NOT NULL,
	source_name TEXT NOT NULL,
	source_sha256 VARCHAR(64) NOT NULL,
	row_count INTEGER NOT NULL,
	metadata_json JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_import_batches_source_sha256 ON import_batches (source_sha256);

CREATE TABLE incidents (
	id VARCHAR(36) NOT NULL,
	external_id VARCHAR(255),
	title TEXT NOT NULL,
	description TEXT NOT NULL,
	year INTEGER,
	date_text TEXT,
	date_start DATE,
	date_precision VARCHAR(32) NOT NULL,
	victim_country TEXT NOT NULL,
	victim_type TEXT NOT NULL,
	impact_area TEXT NOT NULL,
	incident_country TEXT NOT NULL,
	attacker_country TEXT NOT NULL,
	location_name TEXT NOT NULL,
	latitude FLOAT,
	longitude FLOAT,
	location_precision VARCHAR(64) NOT NULL,
	location_source TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	source_url TEXT,
	raw_fields JSON NOT NULL,
	notification_suppressed BOOLEAN NOT NULL,
	qa TEXT NOT NULL,
	reference_number TEXT NOT NULL,
	month TEXT NOT NULL,
	year_end INTEGER,
	victim_identity TEXT NOT NULL,
	victim_type_short TEXT NOT NULL,
	affected_system TEXT NOT NULL,
	method TEXT NOT NULL,
	social_engineering_technique TEXT NOT NULL,
	malware_type TEXT NOT NULL,
	ransomware_type TEXT NOT NULL,
	attack_pattern TEXT NOT NULL,
	motive TEXT NOT NULL,
	motive_short TEXT NOT NULL,
	campaign TEXT NOT NULL,
	impact TEXT NOT NULL,
	impact_short TEXT NOT NULL,
	countermeasures TEXT NOT NULL,
	threat_actor TEXT NOT NULL,
	additional_info TEXT NOT NULL,
	summary TEXT NOT NULL,
	reference_short TEXT NOT NULL,
	google_map_location TEXT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT paired_coordinates CHECK ((latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)),
	CONSTRAINT latitude_range CHECK (latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
	CONSTRAINT longitude_range CHECK (longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
	CONSTRAINT incident_status CHECK (status IN ('published', 'pending')),
	UNIQUE (external_id)
);

CREATE INDEX ix_incidents_created_at ON incidents (created_at);

CREATE INDEX ix_incidents_incident_country ON incidents (incident_country);

CREATE INDEX ix_incidents_reference_number ON incidents (reference_number);

CREATE INDEX ix_incidents_source_url ON incidents (source_url);

CREATE INDEX ix_incidents_status ON incidents (status);

CREATE INDEX ix_incidents_updated_at ON incidents (updated_at);

CREATE INDEX ix_incidents_victim_country ON incidents (victim_country);

CREATE INDEX ix_incidents_year ON incidents (year);

CREATE TABLE processed_updates (
	update_id SERIAL NOT NULL,
	processed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (update_id)
);

CREATE TABLE subscribers (
	chat_id VARCHAR(64) NOT NULL,
	active BOOLEAN NOT NULL,
	source VARCHAR(32) NOT NULL,
	subscribed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (chat_id)
);

CREATE INDEX ix_subscribers_active ON subscribers (active);

CREATE TABLE worker_leases (
	name VARCHAR(64) NOT NULL,
	owner VARCHAR(36) NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (name)
);

CREATE TABLE worker_state (
	name VARCHAR(64) NOT NULL,
	value JSON NOT NULL,
	PRIMARY KEY (name)
);

CREATE TABLE actors (
	id VARCHAR(36) NOT NULL,
	incident_id VARCHAR(36) NOT NULL,
	name TEXT NOT NULL,
	country TEXT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_actors_incident_id ON actors (incident_id);

CREATE TABLE candidates (
	id VARCHAR(36) NOT NULL,
	source_url TEXT NOT NULL,
	title TEXT NOT NULL,
	summary TEXT NOT NULL,
	matched_keywords JSON NOT NULL,
	status VARCHAR(32) NOT NULL,
	baseline BOOLEAN NOT NULL,
	first_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	incident_id VARCHAR(36),
	PRIMARY KEY (id),
	UNIQUE (source_url),
	FOREIGN KEY(incident_id) REFERENCES incidents (id)
);

CREATE INDEX ix_candidates_status ON candidates (status);

CREATE TABLE import_rows (
	id VARCHAR(36) NOT NULL,
	import_batch_id VARCHAR(36) NOT NULL,
	source_row INTEGER NOT NULL,
	incident_id VARCHAR(36),
	raw_fields JSON NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (import_batch_id, source_row),
	FOREIGN KEY(import_batch_id) REFERENCES import_batches (id),
	FOREIGN KEY(incident_id) REFERENCES incidents (id)
);

CREATE INDEX ix_import_rows_import_batch_id ON import_rows (import_batch_id);

CREATE INDEX ix_import_rows_incident_id ON import_rows (incident_id);

CREATE TABLE incident_sources (
	id VARCHAR(36) NOT NULL,
	incident_id VARCHAR(36) NOT NULL,
	url TEXT NOT NULL,
	short_name TEXT NOT NULL,
	raw_text TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (incident_id, url),
	FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_incident_sources_incident_id ON incident_sources (incident_id);

CREATE TABLE locations (
	id VARCHAR(36) NOT NULL,
	incident_id VARCHAR(36) NOT NULL,
	name TEXT NOT NULL,
	country TEXT NOT NULL,
	latitude FLOAT,
	longitude FLOAT,
	precision VARCHAR(64) NOT NULL,
	source TEXT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_locations_incident_id ON locations (incident_id);

CREATE TABLE organisations (
	id VARCHAR(36) NOT NULL,
	incident_id VARCHAR(36) NOT NULL,
	name TEXT NOT NULL,
	country TEXT NOT NULL,
	role VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_organisations_incident_id ON organisations (incident_id);

CREATE TABLE outbox_events (
	id VARCHAR(36) NOT NULL,
	incident_id VARCHAR(36) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	payload JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (incident_id, kind),
	FOREIGN KEY(incident_id) REFERENCES incidents (id)
);

CREATE INDEX ix_outbox_events_incident_id ON outbox_events (incident_id);

CREATE TABLE deliveries (
	id VARCHAR(36) NOT NULL,
	event_id VARCHAR(36) NOT NULL,
	chat_id VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	attempts INTEGER NOT NULL,
	next_attempt_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	lease_owner VARCHAR(36),
	lease_until TIMESTAMP WITHOUT TIME ZONE,
	last_error VARCHAR(64),
	telegram_message_id VARCHAR(64),
	sent_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (event_id, chat_id),
	FOREIGN KEY(event_id) REFERENCES outbox_events (id),
	FOREIGN KEY(chat_id) REFERENCES subscribers (chat_id)
);

CREATE INDEX ix_deliveries_chat_id ON deliveries (chat_id);

CREATE INDEX ix_deliveries_event_id ON deliveries (event_id);

CREATE INDEX ix_deliveries_next_attempt_at ON deliveries (next_attempt_at);

CREATE INDEX ix_deliveries_status ON deliveries (status);
