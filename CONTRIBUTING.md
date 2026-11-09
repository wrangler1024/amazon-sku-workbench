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

Contributions are licensed under Apache-2.0.
