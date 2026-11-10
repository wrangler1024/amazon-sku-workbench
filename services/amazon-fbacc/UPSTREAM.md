# Import provenance

- Original repository: https://github.com/wrangler1024/amazon-fbacc
- Imported commit: `74fa32f703617816934b22bfe628560901e4d457`
- Upstream application tag: `v0.3.0`
- Upstream monitoring tag: `infra-v0.2.0`
- Rate snapshot: `2026-10-31.2`
- Import date: 2026-11-10
- Method: `git subtree add --prefix=services/amazon-fbacc <repository> <commit> --squash`

The module retains its original MIT LICENSE and NOTICE. The monorepo's Apache-2.0
license does not replace that license. Import changes only repository metadata,
maintenance instructions and version recording; calculator/runtime source, UI,
rates, original tests and deployment scripts are preserved.

Development now takes place in this monorepo. The original repository and deployed
service are retained; they are not automatically synchronized or redeployed.
Future selective imports must reconcile local changes and preserve this attribution.
