-- Images belong to the approved artwork delivery, independently of copy publication.
-- Retain optional legacy links and every historical receipt unchanged.
ALTER TABLE image_receipts ALTER COLUMN publication_id DROP NOT NULL;
ALTER TABLE image_receipts ALTER COLUMN target_id DROP NOT NULL;
CREATE INDEX image_receipt_delivery_time ON image_receipts(delivery_id,created_at DESC,id);
CREATE UNIQUE INDEX image_receipt_independent_effective ON image_receipts(delivery_id)
 WHERE publication_id IS NULL AND invalidated_at IS NULL AND withdrawn_at IS NULL;
