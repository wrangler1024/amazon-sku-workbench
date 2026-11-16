# Single-market and European content plans

Approved work items keep their original review market and external binding. Existing
items default to `single`. Only DE, FR and ES owners can explicitly choose `pan_eu`;
DE/FR/ES/IT/NL are then content targets. IT/NL are not added as review entry points.

The ordinary editor and its saved documents remain intact. European listing drafts
and versions are stored per target. One shared brief owns design, composition, layout
and image order, with a language table per image. Saving and AI generation never count
as human confirmation. Generation requires confirmed product facts and uses the
existing authenticated model worker, capacity limits and source-sanitization policies.

One operator action generates the selected kind for all checked targets. Listings
remain localized, while the shared image design is generated once. Batch confirmation
is atomic and idempotent: invalid or stale input cannot partially confirm markets.

One SKU artwork task contains independent target deliveries. Each delivery contains
the complete ordered kit. Review notes, returns, versions and downloads belong to that
market. Main images and textless assets may be reused without a separate shared-image
upload or download entrance. The task is complete only when all selected markets pass.

Editing a shared draft is always allowed. Confirmed changes do not rewrite the version
bound to an in-progress task. Revisions approved during production are queued as a
subsequent batch and activate after the current task finishes. The operator confirms
the common design and target languages once, without per-market brief approvals.

Notifications use the existing transaction outbox and event deduplication. Delivery
and review events identify the target market; the shared task is published once.
The Base projection continues to represent the review source and aggregate task state;
images from a different target language must not overwrite the source-market image.

Release verification uses synthetic records and a disposable database, including
authorization, concurrency, atomic failures, retries, full kits and notification routing.
Production checks preserve existing records, versions, assignments and configuration.

## Deployment and rollback

Apply migration 020 before serving the updated editor. Update API, model, notification
and source-sync processes together. Other workers can keep their deployed versions.
An older frontend cannot overwrite European side-table content through ordinary
single-market endpoints. Existing items are not automatically converted into plans.

Migration 020 expands publication-target uniqueness to include the market. A blind
rollback to an older publication implementation is not compatible after this change.
For a frontend incident retain the new backend/schema and restore the previous plugin
while preparing a forward fix; do not restore an old database over new user activity.
Any backend fallback must retain the marketplace-aware publication implementation.
Record image digests, migration checksums, protected configuration hashes and data
snapshots in the private deployment evidence directory.

One model failure makes the batch unavailable for confirmation; no partial automatic
confirmation is permitted. Review and edit successful candidates before confirming.
Source and competitor identifiers, internal review notes and unconfirmed measurements
use the existing copy checks. The shared prompt contains design once and the selected
language rows in image order; it does not copy source links into production instructions.
