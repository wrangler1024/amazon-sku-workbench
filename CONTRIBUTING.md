# Contributing

Use Node 22.18+ and Python 3.12+. Run `npm run install:apps`, `npm test`, `npm run build`,
and the backend tests described in the README. Keep code, tests and documentation in the
same pull request. Do not commit `.env`, `*.local.json`, database exports, employee data,
production screenshots or built plugin bundles.

Choose the module that owns the behavior. Feishu-specific logic belongs at the adapter
boundary; domain status transitions and permissions belong in the service. Treat production
and integration app versions as deployment metadata, separate from package versions.

For a release update the affected package version(s), lockfile(s), `versions.json`, and
`CHANGELOG.md`. CI checks these versions. Preserve existing APIs or document migration steps.
Do not rewrite an applied SQL migration; add the next numbered migration.

Cloud-mail changes use `npm run test:mail` and `npm run build:mail`, and must preserve
mailbox grants, admission, supervisor pilot restrictions and notification recipient
checks. Test only with temporary SQLite and mocked IMAP/Feishu. Unified workbench
login and database migration are separate work; never copy deployment databases,
emails or encryption keys into this public repository.

Contributions are licensed under Apache-2.0 except those in services/amazon-fbacc,
which remain MIT licensed. Preserve the module's LICENSE, NOTICE and provenance.
For FBACC changes also run its rate validator, Python monitoring tests, reference
samples and `npm run test:fbacc:integration`; see docs/fbacc.md. Routine work does
not authorize new fee publication, enabling the monitor timer or production deployment.
