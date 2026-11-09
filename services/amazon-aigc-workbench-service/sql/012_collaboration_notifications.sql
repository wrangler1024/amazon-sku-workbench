-- Transactional notifications; only new collaboration events are queued, never historical backfill.
CREATE TABLE notification_outbox (
 id uuid PRIMARY KEY, event_id bigint REFERENCES audit_events,
 work_item_id uuid REFERENCES work_items, task_id uuid REFERENCES artwork_tasks,
 recipient_id uuid NOT NULL REFERENCES users, action text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','sending','sent','failed','skipped')),
 attempts integer NOT NULL DEFAULT 0, run_after timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, lease_token uuid, message_id text, error text,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(event_id,recipient_id)
);
CREATE INDEX notification_poll ON notification_outbox(state,run_after);
CREATE TABLE notification_config (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), enabled boolean NOT NULL DEFAULT false,
 updated_at timestamptz NOT NULL DEFAULT now(), updated_by uuid REFERENCES users
);
INSERT INTO notification_config(singleton,enabled) VALUES(true,false);
