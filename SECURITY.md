# Security

Do not include credentials, screenshots with real customer data, private Feishu identifiers,
or employee records in issues. Report vulnerabilities through this repository's private
GitHub security advisory channel when enabled.

The API enforces user/role scope, revision checks, task ownership and explicit target
allowlists. Configure the exact Feishu application, Base, identity table and plugin origin
before enabling production. Do not replace origin checks with wildcards or expose a local
demo session publicly. Each deployment uses a separate database and object storage root.

OpenAI, DeepSeek, collection service and Feishu secrets stay on the server. The repository
contains synthetic fixtures only. `scripts/check_public.py` and a credential scanner such
as Gitleaks should run before publishing any change.

No automatic production deployment is configured in CI. Database migrations are append-only;
back up the database and original image storage before upgrading.
