CREATE TABLE legacy_first_reviews (
 base_id text NOT NULL, table_id text NOT NULL, record_id text NOT NULL,
 artist_id uuid NOT NULL REFERENCES users(id),
 result text NOT NULL CHECK(result IN ('accepted','rejected')),
 review_rev bigint NOT NULL, review_time timestamptz NOT NULL,
 source_sha256 text NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(base_id,table_id,record_id,artist_id),
 FOREIGN KEY(base_id,table_id,record_id) REFERENCES legacy_migration_records(base_id,table_id,record_id)
);
