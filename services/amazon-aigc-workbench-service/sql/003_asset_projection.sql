CREATE TABLE external_asset_bindings (
 asset_id uuid NOT NULL REFERENCES assets, provider text NOT NULL, base_id text NOT NULL,
 file_token text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(asset_id,provider,base_id)
);
ALTER TABLE artwork_tasks ADD COLUMN assigned_at timestamptz;
