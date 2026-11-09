-- Keep the Amazon target independent of whether copy/images were correctly registered.
CREATE TABLE publication_targets (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES content_documents,
 store text NOT NULL, msku text NOT NULL, asin text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(document_id,store,msku)
);
INSERT INTO publication_targets(id,document_id,store,msku,asin,created_at)
 SELECT gen_random_uuid(),document_id,store,msku,asin,created_at FROM
 (SELECT DISTINCT ON (document_id,store,msku) document_id,store,msku,asin,created_at
 FROM publication_records ORDER BY document_id,store,msku,created_at DESC,id DESC) p;
ALTER TABLE content_documents ADD COLUMN publication_target_id uuid REFERENCES publication_targets;
ALTER TABLE publication_records ADD COLUMN target_id uuid REFERENCES publication_targets;
ALTER TABLE publication_records ADD COLUMN withdrawn_at timestamptz;
ALTER TABLE publication_records ADD COLUMN withdrawn_by uuid REFERENCES users;
ALTER TABLE publication_records ADD COLUMN withdrawal_reason text;
UPDATE publication_records p SET target_id=t.id FROM publication_targets t
 WHERE t.document_id=p.document_id AND t.store=p.store AND t.msku=p.msku;
UPDATE content_documents d SET publication_target_id=(SELECT p.target_id FROM publication_records p
 WHERE p.document_id=d.id ORDER BY p.created_at DESC,p.id DESC LIMIT 1);
ALTER TABLE publication_records ALTER COLUMN target_id SET NOT NULL;
ALTER TABLE image_receipts ADD COLUMN target_id uuid REFERENCES publication_targets;
ALTER TABLE image_receipts ADD COLUMN withdrawn_at timestamptz;
ALTER TABLE image_receipts ADD COLUMN withdrawn_by uuid REFERENCES users;
ALTER TABLE image_receipts ADD COLUMN withdrawal_reason text;
UPDATE image_receipts r SET target_id=p.target_id FROM publication_records p WHERE p.id=r.publication_id;
ALTER TABLE image_receipts ALTER COLUMN target_id SET NOT NULL;
-- Store re-registrations as new events instead of overwriting previous receipts.
ALTER TABLE image_receipts DROP CONSTRAINT image_receipts_publication_id_delivery_id_key;
UPDATE image_receipts r SET invalidated_at=now() WHERE r.invalidated_at IS NULL AND EXISTS(
 SELECT 1 FROM image_receipts n WHERE n.target_id=r.target_id AND n.delivery_id=r.delivery_id
 AND n.invalidated_at IS NULL AND (n.created_at,n.id)>(r.created_at,r.id));
CREATE UNIQUE INDEX image_receipt_effective ON image_receipts(target_id,delivery_id)
 WHERE invalidated_at IS NULL AND withdrawn_at IS NULL;
CREATE INDEX publication_active ON publication_records(target_id,created_at DESC) WHERE withdrawn_at IS NULL;
CREATE INDEX image_receipt_target ON image_receipts(target_id,created_at DESC);
