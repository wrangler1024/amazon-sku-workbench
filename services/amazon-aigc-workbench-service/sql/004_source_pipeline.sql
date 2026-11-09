ALTER TABLE jobs DROP CONSTRAINT jobs_state_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_state_check CHECK(state IN ('queued','waiting_sources','waiting_input','running','succeeded','failed'));
CREATE INDEX source_job_lookup ON jobs(work_item_id,(payload->>'url'),created_at DESC) WHERE kind='source';
