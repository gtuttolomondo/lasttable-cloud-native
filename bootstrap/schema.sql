CREATE TABLE schema_migrations (version NUMBER PRIMARY KEY, checksum VARCHAR2(64) NOT NULL, applied_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL);
CREATE TABLE demo_sessions (session_id VARCHAR2(36) PRIMARY KEY, title VARCHAR2(100) NOT NULL, accepting NUMBER(1) DEFAULT 1 NOT NULL CHECK (accepting IN (0,1)), created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL);
CREATE TABLE restaurants (restaurant_id VARCHAR2(32) PRIMARY KEY, name VARCHAR2(100) NOT NULL, cuisine VARCHAR2(80) NOT NULL, area VARCHAR2(80) NOT NULL, description VARCHAR2(600) NOT NULL, icon VARCHAR2(20 CHAR), price_label VARCHAR2(20));
CREATE SEQUENCE booking_alias_seq START WITH 1 INCREMENT BY 1 CACHE 100;
CREATE TABLE bookings (
 booking_id VARCHAR2(36) PRIMARY KEY,
 public_alias VARCHAR2(24) UNIQUE NOT NULL,
 session_id VARCHAR2(36) REFERENCES demo_sessions(session_id) NOT NULL,
 restaurant_id VARCHAR2(32) REFERENCES restaurants(restaurant_id) NOT NULL,
 nickname VARCHAR2(60 CHAR) NOT NULL,
 recipient_email VARCHAR2(320) NOT NULL,
 party_size NUMBER NOT NULL CHECK (party_size BETWEEN 1 AND 12),
 reservation_time VARCHAR2(5) NOT NULL,
 booking_state VARCHAR2(20) DEFAULT 'PENDING' NOT NULL CHECK (booking_state IN ('PENDING','CONFIRMED')),
 state_version NUMBER DEFAULT 0 NOT NULL,
 created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 confirmed_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX bookings_session_idx ON bookings(session_id, created_at);
CREATE TABLE outbox_events (
 event_id VARCHAR2(36) PRIMARY KEY,
 aggregate_id VARCHAR2(36) NOT NULL,
 aggregate_version NUMBER NOT NULL,
 event_type VARCHAR2(64) NOT NULL,
 topic VARCHAR2(200) NOT NULL,
 payload CLOB CHECK (payload IS JSON),
 session_id VARCHAR2(36) NOT NULL,
 correlation_id VARCHAR2(36) NOT NULL,
 created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 published_at TIMESTAMP WITH TIME ZONE,
 partition_id NUMBER,
 source_offset NUMBER,
 CONSTRAINT outbox_aggregate_uk UNIQUE (aggregate_id, event_type, aggregate_version)
);
CREATE INDEX outbox_pending_idx ON outbox_events(published_at, created_at);
CREATE TABLE processed_events (
 processor_name VARCHAR2(40) NOT NULL,
 projection_id VARCHAR2(64) NOT NULL,
 event_id VARCHAR2(36) NOT NULL,
 session_id VARCHAR2(36) NOT NULL,
 restaurant_id VARCHAR2(32),
 source_topic VARCHAR2(200),
 partition_id NUMBER,
 source_offset NUMBER,
 processed_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 PRIMARY KEY (processor_name, projection_id, event_id)
);
CREATE INDEX processed_source_idx ON processed_events(session_id, processor_name, projection_id, partition_id, source_offset);
CREATE TABLE restaurant_stats (
 session_id VARCHAR2(36) NOT NULL,
 projection_id VARCHAR2(64) NOT NULL,
 restaurant_id VARCHAR2(32) NOT NULL,
 booking_count NUMBER DEFAULT 0 NOT NULL,
 updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 PRIMARY KEY (session_id, projection_id, restaurant_id)
);
CREATE TABLE notification_delivery (
 event_id VARCHAR2(36) NOT NULL,
 recipient_ref VARCHAR2(36) NOT NULL,
 channel VARCHAR2(20) NOT NULL,
 booking_id VARCHAR2(36) NOT NULL,
 notification_state VARCHAR2(20) NOT NULL CHECK (notification_state IN ('PENDING','SUBMITTED','FAILED','UNKNOWN')),
 attempt_count NUMBER DEFAULT 0 NOT NULL,
 lease_token VARCHAR2(36),
 lease_expires_at TIMESTAMP WITH TIME ZONE,
 error_code VARCHAR2(64),
 message_id VARCHAR2(240),
 smtp_submitted_at TIMESTAMP WITH TIME ZONE,
 created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 PRIMARY KEY (event_id, recipient_ref, channel)
);
CREATE INDEX notifications_booking_idx ON notification_delivery(booking_id);
CREATE TABLE demo_events (
 log_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 session_id VARCHAR2(36) NOT NULL,
 booking_id VARCHAR2(36),
 public_alias VARCHAR2(24),
 correlation_id VARCHAR2(36),
 event_id VARCHAR2(36),
 component VARCHAR2(50) NOT NULL,
 action VARCHAR2(80) NOT NULL,
 outcome VARCHAR2(40),
 detail VARCHAR2(400),
 topic VARCHAR2(200),
 group_id VARCHAR2(100),
 partition_id NUMBER,
 source_offset NUMBER,
 occurred_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
CREATE INDEX demo_events_session_idx ON demo_events(session_id, occurred_at);
CREATE TABLE demo_controls (
 session_id VARCHAR2(36) PRIMARY KEY,
 notifications_pause_requested NUMBER(1) DEFAULT 0 NOT NULL CHECK (notifications_pause_requested IN (0,1)),
 updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
CREATE TABLE worker_status (
 worker_id VARCHAR2(100) PRIMARY KEY,
 component VARCHAR2(40) NOT NULL,
 session_id VARCHAR2(36),
 worker_state VARCHAR2(40) NOT NULL,
 last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
CREATE TABLE replay_runs (
 run_id VARCHAR2(36) PRIMARY KEY,
 session_id VARCHAR2(36) NOT NULL,
 projection_id VARCHAR2(64) NOT NULL UNIQUE,
 group_id VARCHAR2(100) NOT NULL UNIQUE,
 run_state VARCHAR2(20) NOT NULL CHECK (run_state IN ('REQUESTED','RUNNING','COMPLETE','FAILED')),
 requested_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 completed_at TIMESTAMP WITH TIME ZONE,
 cutoffs_json CLOB CHECK (cutoffs_json IS JSON),
 expected_json CLOB CHECK (expected_json IS JSON),
 actual_json CLOB CHECK (actual_json IS JSON),
 records_processed NUMBER DEFAULT 0 NOT NULL,
 error_code VARCHAR2(100)
);
CREATE OR REPLACE VIEW booking_recipients AS
 SELECT b.booking_id AS recipient_ref, b.booking_id, b.recipient_email, b.public_alias,
 b.session_id, b.nickname, b.party_size, b.reservation_time, r.name AS restaurant_name
 FROM bookings b JOIN restaurants r ON r.restaurant_id = b.restaurant_id;
