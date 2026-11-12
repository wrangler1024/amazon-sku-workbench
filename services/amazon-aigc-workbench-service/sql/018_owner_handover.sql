CREATE TABLE source_owner_state (
 base_id text NOT NULL, table_id text NOT NULL, record_id text NOT NULL,
 owner_subject text, owner_name text, submitter_id text,
 group_name text, approval_group text, sku text, record_number text, site text,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(base_id,table_id,record_id)
);

CREATE TABLE owner_handover_logs (
 id uuid PRIMARY KEY, batch_id uuid NOT NULL, change_key text NOT NULL UNIQUE,
 base_id text NOT NULL, table_id text NOT NULL, record_id text NOT NULL,
 work_item_id uuid REFERENCES work_items ON DELETE SET NULL,
 sku text, record_number text, site text,
 old_owner_subject text, old_owner_name text, old_submitter_id text,
 old_group text, old_approval_group text,
 new_owner_subject text NOT NULL, new_owner_name text, new_submitter_id text,
 new_group text, new_approval_group text,
 base_state text NOT NULL DEFAULT 'pending'
  CHECK(base_state IN ('pending','succeeded','failed','superseded','not_applicable')),
 workbench_state text NOT NULL DEFAULT 'pending'
  CHECK(workbench_state IN ('pending','succeeded','failed','superseded','not_applicable')),
 state text NOT NULL DEFAULT 'pending'
  CHECK(state IN ('pending','succeeded','failed','superseded')),
 attempts integer NOT NULL DEFAULT 0, error text, base_log_record_id text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz
);

CREATE INDEX owner_handover_pending ON owner_handover_logs(state,created_at,id)
 WHERE state IN ('pending','failed');
CREATE INDEX owner_handover_record ON owner_handover_logs(record_id,created_at DESC);
