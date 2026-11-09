ALTER TABLE collector_configs DROP CONSTRAINT collector_configs_provider_check;
ALTER TABLE collector_configs ADD CONSTRAINT collector_configs_provider_check CHECK(provider IN ('firecrawl','tmapi'));
CREATE UNIQUE INDEX one_enabled_collector ON collector_configs ((enabled)) WHERE enabled;
