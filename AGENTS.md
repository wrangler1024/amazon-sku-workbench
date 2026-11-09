# Agent instructions

Read README.md, docs/architecture.md and docs/versioning.md before changes.
Inspect Git status and preserve unrelated changes. This repository is public: never copy
private environment files, tenant identifiers, employee rosters, backups or production evidence
into it. Use synthetic fixtures. Never point tests at production data.

Do not change a live Feishu application or deploy a service as part of an ordinary code change.
Build, test and document the intended change first; deployment is a separate user-authorized task.
Keep frontend editing drafts, review notes and uploads safe from polling. Manual confirmation
must not fabricate a product-facts confirmation; AI generation requires confirmed facts.
