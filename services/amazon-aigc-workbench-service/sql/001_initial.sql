CREATE TABLE users (
 id uuid PRIMARY KEY, name text NOT NULL, roles text[] NOT NULL DEFAULT '{}', groups text[] NOT NULL DEFAULT '{}',
 active boolean NOT NULL DEFAULT false, internal boolean NOT NULL DEFAULT false, verified_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE external_identities (
 provider text NOT NULL, tenant text NOT NULL, subject text NOT NULL, user_id uuid NOT NULL REFERENCES users,
 PRIMARY KEY(provider, tenant, subject)
);
CREATE TABLE sessions (
 token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users, expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE work_items (
 id uuid PRIMARY KEY, environment text NOT NULL, site text NOT NULL, code text NOT NULL, name text NOT NULL,
 owner_id uuid REFERENCES users, group_name text NOT NULL, review_status text NOT NULL,
 source_revision text, review_snapshot jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE external_bindings (
 provider text NOT NULL, tenant text NOT NULL, environment text NOT NULL, base_id text NOT NULL,
 table_id text NOT NULL, record_id text NOT NULL, work_item_id uuid NOT NULL REFERENCES work_items,
 PRIMARY KEY(provider,tenant,environment,base_id,table_id,record_id), UNIQUE(work_item_id,provider)
);
CREATE TABLE content_documents (
 id uuid PRIMARY KEY, work_item_id uuid NOT NULL UNIQUE REFERENCES work_items, schema_version integer NOT NULL DEFAULT 1,
 revision integer NOT NULL DEFAULT 0, body jsonb NOT NULL DEFAULT '{}', facts_version uuid, listing_version uuid,
 brief_version uuid, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE content_versions (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES content_documents, kind text NOT NULL CHECK(kind IN ('facts','listing','brief')),
 body jsonb NOT NULL, facts_version uuid REFERENCES content_versions, created_by uuid NOT NULL REFERENCES users,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE content_documents ADD FOREIGN KEY(facts_version) REFERENCES content_versions;
ALTER TABLE content_documents ADD FOREIGN KEY(listing_version) REFERENCES content_versions;
ALTER TABLE content_documents ADD FOREIGN KEY(brief_version) REFERENCES content_versions;
CREATE TABLE artwork_tasks (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES content_documents, brief_version uuid NOT NULL REFERENCES content_versions,
 batch integer NOT NULL, state text NOT NULL CHECK(state IN ('pool','waiting','working','pending','revision','accepted','withdrawn')),
 assignee_id uuid REFERENCES users, assigned_by uuid REFERENCES users, urgent boolean NOT NULL DEFAULT false,
 due_date date, revision integer NOT NULL DEFAULT 0, current_delivery_id uuid, first_review_result text,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, UNIQUE(document_id,batch),
 CHECK ((state='pool' AND assignee_id IS NULL) OR state <> 'pool')
);
CREATE UNIQUE INDEX one_active_artwork ON artwork_tasks(document_id) WHERE state NOT IN ('accepted','withdrawn');
CREATE INDEX artwork_assignee_state ON artwork_tasks(assignee_id,state);
CREATE TABLE assets (
 id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items, uploaded_by uuid NOT NULL REFERENCES users,
 object_key text NOT NULL UNIQUE, name text NOT NULL, sha256 text NOT NULL, size_bytes bigint NOT NULL CHECK(size_bytes>0),
 mime text NOT NULL, width integer, height integer, source_url text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE artwork_deliveries (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES artwork_tasks, version integer NOT NULL, asset_ids uuid[] NOT NULL,
 note text NOT NULL DEFAULT '', delivered_by uuid NOT NULL REFERENCES users, delivered_at timestamptz NOT NULL DEFAULT now(),
 result text CHECK(result IN ('accepted','rejected')), review_note text, reviewed_by uuid REFERENCES users, reviewed_at timestamptz,
 UNIQUE(task_id,version)
);
ALTER TABLE artwork_tasks ADD FOREIGN KEY(current_delivery_id) REFERENCES artwork_deliveries;
CREATE TABLE publication_records (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES content_documents, listing_version uuid NOT NULL REFERENCES content_versions,
 store text NOT NULL, msku text NOT NULL, asin text NOT NULL DEFAULT '', receipt text NOT NULL,
 created_by uuid NOT NULL REFERENCES users, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE image_receipts (
 id uuid PRIMARY KEY, publication_id uuid NOT NULL REFERENCES publication_records, delivery_id uuid NOT NULL REFERENCES artwork_deliveries,
 receipt text NOT NULL, created_by uuid NOT NULL REFERENCES users, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(publication_id,delivery_id)
);
CREATE TABLE source_snapshots (
 id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items, source_id text NOT NULL, url text NOT NULL,
 document_revision integer NOT NULL, method text NOT NULL, status text NOT NULL, body jsonb NOT NULL,
 object_key text, parser_version text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE jobs (
 id uuid PRIMARY KEY, kind text NOT NULL, work_item_id uuid REFERENCES work_items, actor_id uuid REFERENCES users,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','failed')),
 payload jsonb NOT NULL, result jsonb, error text, attempts integer NOT NULL DEFAULT 0,
 run_after timestamptz NOT NULL DEFAULT now(), lease_until timestamptz, lease_token uuid,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX jobs_poll ON jobs(kind,state,run_after);
CREATE TABLE sync_outbox (
 id bigserial PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','failed')),
 attempts integer NOT NULL DEFAULT 0, error text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX outbox_order ON sync_outbox(work_item_id,id);
CREATE TABLE model_calls (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs, provider text NOT NULL, model text NOT NULL,
 task text NOT NULL, prompt_version text NOT NULL, usage jsonb NOT NULL DEFAULT '{}', status text NOT NULL,
 request_id text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_events (
 id bigserial PRIMARY KEY, work_item_id uuid REFERENCES work_items, actor_id uuid REFERENCES users,
 action text NOT NULL, detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE idempotency_keys (
 actor_id uuid NOT NULL REFERENCES users, key text NOT NULL, request_hash text NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor_id,key)
);
CREATE TABLE review_snapshots (
 id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items, fingerprint text NOT NULL,
 body jsonb NOT NULL, captured_at timestamptz NOT NULL DEFAULT now(), UNIQUE(work_item_id,fingerprint)
);
