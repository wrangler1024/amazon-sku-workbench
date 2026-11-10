# Cloud-mail module instructions

Read the module README and root architecture/versioning documents before edits.
This is a public source snapshot; never import the private repository history,
tenant identifiers, employee rosters, mailbox credentials, real email content,
databases, backups or internal release reports.

Preserve mailbox grants, supervisor-only pilot policy, admission, member revocation,
notification recipient checks and per-user read/flag state. Unified workbench login
and database migration require a separate task. Tests must use temporary SQLite,
synthetic accounts and mocked IMAP/Feishu; never run tests on deployed data.

Run the module tests and verify the installed wheel includes static files and
OpenAPI after packaging changes. Offline UI work uses scripts/dashboard_demo.py;
serve --no-scheduler still starts notification workers and is not an offline demo.
Do not deploy or enable notifications as part of ordinary repository maintenance.
