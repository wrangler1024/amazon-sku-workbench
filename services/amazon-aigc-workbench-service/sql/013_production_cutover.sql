-- Immutable source evidence and explicit legacy cohorts; no synthetic business events.
CREATE TABLE legacy_migration_records (
 base_id text NOT NULL, table_id text NOT NULL, record_id text NOT NULL,
 cohort text NOT NULL CHECK(cohort IN ('reset','active','history','unapproved')),
 fingerprint text NOT NULL, snapshot jsonb NOT NULL,
 work_item_id uuid REFERENCES work_items, artist_id uuid REFERENCES users,
 first_started_at timestamptz, captured_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(base_id,table_id,record_id)
);
ALTER TABLE artwork_tasks ADD COLUMN legacy_takeover boolean NOT NULL DEFAULT false;
CREATE TABLE legacy_attachment_links (
 asset_id uuid PRIMARY KEY REFERENCES assets, attachment jsonb NOT NULL,
 record_id text NOT NULL, field_name text NOT NULL, is_archive boolean NOT NULL DEFAULT false
);
