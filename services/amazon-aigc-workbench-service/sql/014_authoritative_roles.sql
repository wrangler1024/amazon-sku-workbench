ALTER TABLE users ADD COLUMN roles_managed boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN roles_synced_at timestamptz;
