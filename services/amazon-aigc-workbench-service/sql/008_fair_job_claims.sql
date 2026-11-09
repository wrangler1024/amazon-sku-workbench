ALTER TABLE jobs ADD COLUMN started_at timestamptz;
CREATE INDEX jobs_actor_schedule ON jobs(kind, actor_id, started_at DESC);
CREATE INDEX jobs_running_actor ON jobs(kind, actor_id) WHERE state='running';
CREATE INDEX jobs_model_daily_created ON jobs(created_at) WHERE kind='model';
