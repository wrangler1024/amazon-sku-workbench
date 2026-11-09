CREATE TABLE provider_configs (
 provider text PRIMARY KEY CHECK(provider IN ('openai','deepseek')),
 key_ciphertext text NOT NULL,
 model text NOT NULL,
 enabled boolean NOT NULL DEFAULT false,
 daily_limit integer NOT NULL DEFAULT 50 CHECK(daily_limit BETWEEN 1 AND 10000),
 updated_by uuid NOT NULL REFERENCES users,
 updated_at timestamptz NOT NULL DEFAULT now()
);
