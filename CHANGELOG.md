# Changelog

## Unreleased — Planning only

- Document the ERP material-creation workflow and independent module boundary,
  including unit/cost confirmation, material reuse, partial-failure recovery,
  Lingxing capability checks and future migration away from Feishu.
- Reserve `services/amazon-erp-material-service` and `integrations/lingxing` for
  documentation only. No executable code, API, schema, UI or deployment change;
  existing package versions and runtime behavior remain unchanged.

## Bundle 0.2.0 — 2026-11-10

- Import FBACC 0.3.0 as `services/amazon-fbacc`, retaining its MIT license,
  source provenance, independent calculator UI/API and 17-market rate snapshot.
- Record monitoring toolkit 0.2.0 and rate snapshot 2026-10-31.2 independently;
  calculation code and fee data are unchanged from the upstream baseline.
- Review 0.70.2: align the local AE proxy with FBACC port 4318 and load its
  server-side environment configuration. Production endpoint behavior is unchanged.
- Add real review-adapter/proxy/calculator integration verification, module CI,
  independent container startup and Git-only standalone release packaging.
- Editor/API remain 0.21.0. No database migration, live deployment, Feishu
  publication or rate-monitor scheduling change is included.

## Bundle 0.1.0 — 2026-11-09

First public source baseline, released under Apache-2.0.

- Review 0.70.1: product selection, marketplace cost estimates, rationale and review queues.
- Editor 0.21.0: product facts, manual/AI Listing and artwork briefs, task allocation,
  delivery/review, separate copy/image publication records and historical queries.
- API 0.21.0: PostgreSQL domain model, asynchronous generation/collection, permissions,
  versioned assets, Feishu adapters and migration/export tooling.
- Public configuration templates replace company-specific endpoints and identifiers.
- Tests use synthetic product/employee data and a disposable PostgreSQL schema.

This is a sanitized source baseline. Enterprise application release numbers are not public
package versions, and no private Git history or production records are included.
