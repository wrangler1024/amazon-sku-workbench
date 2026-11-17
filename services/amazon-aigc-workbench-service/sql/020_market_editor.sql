-- Existing single-market documents, versions and deliveries remain unchanged.
ALTER TABLE work_items ADD COLUMN editor_mode text NOT NULL DEFAULT 'single'
 CHECK (editor_mode IN ('single','pan_eu'));
ALTER TABLE work_items ADD COLUMN editor_targets text[] NOT NULL DEFAULT '{}';
ALTER TABLE work_items ADD CONSTRAINT editor_mode_source
 CHECK (editor_mode='single' OR site IN ('DE','FR','ES'));
ALTER TABLE work_items ADD CONSTRAINT editor_targets_allowed
 CHECK (editor_targets <@ ARRAY['DE','FR','ES','IT','NL']::text[] AND cardinality(editor_targets)<=5);

CREATE TABLE market_plans (
 document_id uuid PRIMARY KEY REFERENCES content_documents,
 shared_brief jsonb NOT NULL DEFAULT '{}',
 generation_targets jsonb NOT NULL DEFAULT '{}',
 shared_version uuid REFERENCES content_versions,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE market_contents (
 document_id uuid NOT NULL REFERENCES market_plans,
 site text NOT NULL CHECK (site IN ('DE','FR','ES','IT','NL')),
 listing jsonb NOT NULL DEFAULT '{}', listing_review jsonb NOT NULL DEFAULT '{}',
 listing_version uuid REFERENCES content_versions,
 image_texts jsonb NOT NULL DEFAULT '[]',
 PRIMARY KEY(document_id,site)
);
ALTER TABLE artwork_tasks ADD COLUMN market_sites text[] NOT NULL DEFAULT '{}';
ALTER TABLE artwork_deliveries ADD COLUMN marketplace text;
ALTER TABLE artwork_deliveries ADD COLUMN market_version integer CHECK (market_version>0);
CREATE UNIQUE INDEX one_market_delivery_version
 ON artwork_deliveries(task_id,marketplace,market_version) WHERE marketplace IS NOT NULL;
CREATE TABLE artwork_task_markets (
 task_id uuid NOT NULL REFERENCES artwork_tasks,
 site text NOT NULL CHECK (site IN ('DE','FR','ES','IT','NL')),
 state text NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting','working','pending','revision','accepted')),
 revision integer NOT NULL DEFAULT 0,
 draft_asset_ids uuid[] NOT NULL DEFAULT '{}', draft_note text NOT NULL DEFAULT '',
 current_delivery_id uuid REFERENCES artwork_deliveries,
 first_review_result text CHECK(first_review_result IN ('accepted','rejected')),
 PRIMARY KEY(task_id,site)
);
CREATE INDEX market_reviews_pending ON artwork_task_markets(task_id) WHERE state='pending';
CREATE TABLE market_submissions (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES market_plans,
 brief_version uuid NOT NULL REFERENCES content_versions,
 sites text[] NOT NULL,
 state text NOT NULL CHECK(state IN ('queued','published')),
 task_id uuid REFERENCES artwork_tasks,
 created_by uuid NOT NULL REFERENCES users,
 urgent boolean NOT NULL DEFAULT false, due_date date,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(document_id,brief_version)
);
CREATE INDEX queued_market_submissions ON market_submissions(document_id,created_at) WHERE state='queued';
ALTER TABLE publication_targets ADD COLUMN marketplace text NOT NULL DEFAULT '';
ALTER TABLE publication_targets DROP CONSTRAINT publication_targets_document_id_store_msku_key;
ALTER TABLE publication_targets ADD UNIQUE(document_id,store,msku,marketplace);
ALTER TABLE market_contents ADD COLUMN publication_target_id uuid REFERENCES publication_targets;
