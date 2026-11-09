-- Zero is an explicit unlimited daily-call setting. Existing counters are retained.
ALTER TABLE provider_configs DROP CONSTRAINT provider_configs_daily_limit_check;
ALTER TABLE provider_configs ADD CONSTRAINT provider_configs_daily_limit_check
 CHECK (daily_limit BETWEEN 0 AND 10000);
ALTER TABLE provider_configs ALTER COLUMN daily_limit SET DEFAULT 0;
