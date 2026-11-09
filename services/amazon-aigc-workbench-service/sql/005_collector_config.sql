CREATE TABLE collector_configs (
 provider text PRIMARY KEY CHECK(provider='firecrawl'),
 key_ciphertext text NOT NULL,
 enabled boolean NOT NULL DEFAULT false,
 daily_limit integer NOT NULL DEFAULT 100 CHECK(daily_limit BETWEEN 1 AND 10000),
 updated_by uuid NOT NULL REFERENCES users,
 updated_at timestamptz NOT NULL DEFAULT now()
);
