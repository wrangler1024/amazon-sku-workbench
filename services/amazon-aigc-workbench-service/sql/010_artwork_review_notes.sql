ALTER TABLE artwork_deliveries ADD COLUMN image_notes jsonb NOT NULL DEFAULT '{}';
ALTER TABLE artwork_deliveries ADD COLUMN canceled_reviews jsonb NOT NULL DEFAULT '[]';
ALTER TABLE image_receipts ADD COLUMN invalidated_at timestamptz;
