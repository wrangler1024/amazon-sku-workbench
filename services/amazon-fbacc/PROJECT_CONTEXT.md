# Project context

amazon-fbacc is a standalone FBA fee calculator and official-source monitoring toolkit.
The application is0.3.0, monitoring infra is0.2.0, and the bundled reviewed rate
snapshot is2026-10-31.2. It supports17 domestic ordinary-goods marketplaces.

Node.js22.14+ runs the calculator with no package dependencies. Optional Python
monitoring has an isolated, hash-pinned pypdf dependency. Sources and limits are
recorded in docs/sources.md and README.md.

The monitor fetches public sources, extracts differences, prepares a copy of
active rates as a draft and validates isolated workspaces. New fee values and
rules still need human review. Samples use reviewed reference amounts, not a
live official quotation API. Approval/publishing is separate from monitoring.

The public repository excludes raw official documents and private deployment
artifacts. Existing installations are managed separately; repository changes do
not imply deployment or enabling scheduled checks.

Version0.2.1 scopes low-price controls to each marketplace. US/CA/MX have no selector, AU has two choices, UK/EU show metadata-based thresholds. Comparison eligibility is independent per market. API schemas/defaults and rate data are unchanged.

Version0.3.0 adds AE/SA/JP. New markets automatically choose native-price bands. JP has integer-yen inputs/outputs and sum/side constraints; ambiguous AE/SA cases return explicit quote_required. Monitoring remains manual; its timer must stay disabled.
