CREATE TABLE base_workflow_events (
    event_id text PRIMARY KEY,
    revision bigint NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    error text,
    received_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE TABLE base_workflow_records (
    record_id text PRIMARY KEY,
    snapshot jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE base_workflow_runs (
    run_key text PRIMARY KEY,
    event_id text NOT NULL,
    workflow_id text NOT NULL,
    record_id text NOT NULL,
    submission_id text NOT NULL DEFAULT '',
    occurred_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'queued',
    execution jsonb NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX base_workflow_runs_queue ON base_workflow_runs(state,created_at);
CREATE TABLE base_workflow_operations (
    operation_key text PRIMARY KEY,
    run_key text NOT NULL REFERENCES base_workflow_runs(run_key),
    record_id text NOT NULL,
    patch jsonb NOT NULL,
    state text NOT NULL DEFAULT 'prepared',
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE TABLE base_workflow_notices (
    notice_key text PRIMARY KEY,
    run_key text NOT NULL REFERENCES base_workflow_runs(run_key),
    record_id text NOT NULL,
    recipient text NOT NULL,
    payload jsonb NOT NULL,
    guards jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    error text,
    message_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz
);
CREATE INDEX base_workflow_notices_queue ON base_workflow_notices(state,created_at);
CREATE TABLE base_workflow_control (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled boolean NOT NULL DEFAULT false,
    seeded_at timestamptz,
    heartbeat_at timestamptz,
    last_event_at timestamptz,
    last_reconcile_at timestamptz,
    listener_alive boolean NOT NULL DEFAULT false,
    listener_error text,
    config_hash text
);
INSERT INTO base_workflow_control(singleton) VALUES(true);
