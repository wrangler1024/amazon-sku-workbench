-- Read-path indexes only. Preserve all business rows and historical versions.
CREATE INDEX work_items_owner_created ON work_items(owner_id,created_at DESC,id);
CREATE INDEX work_items_group_created ON work_items(group_name,created_at DESC,id);
CREATE INDEX work_items_created ON work_items(created_at DESC,id);
CREATE INDEX assets_item_source_created ON assets(work_item_id,source_url,created_at,id);
CREATE INDEX versions_document_created ON content_versions(document_id,created_at DESC,id);
CREATE INDEX sources_item_created ON source_snapshots(work_item_id,created_at DESC);
CREATE INDEX jobs_item_created ON jobs(work_item_id,created_at DESC);
CREATE INDEX audit_item_id ON audit_events(work_item_id,id DESC);
CREATE INDEX publication_document_created ON publication_records(document_id,created_at DESC,id);
