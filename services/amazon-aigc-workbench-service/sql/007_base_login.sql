CREATE TABLE base_login_challenges (
 id uuid PRIMARY KEY, secret_hash text NOT NULL, issued_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '2 minutes', attempts int NOT NULL DEFAULT 0,
 used_at timestamptz, record_id text, user_id uuid REFERENCES users
);
CREATE INDEX base_login_expiry ON base_login_challenges(expires_at);
