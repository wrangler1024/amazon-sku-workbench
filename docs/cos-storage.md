# COS originals and backup boundaries

API 0.27.0 optionally publishes immutable originals to private COS storage. The
application keeps existing asset UUIDs, object keys, permissions and delivery
versions. Downloads still pass through the authenticated API, including ordered
historical ZIP downloads. ZIPs stream originals into at most two temporary archives,
with cleanup on completion or client disconnect, keeping memory and temporary disk bounded.
SQL 020 remains compatible; no business rows are moved.

Set `AIGC_COS_CONFIG_FILE` to a private JSON file, or place it at
`storage_root.parent/private/cos-media.json`. Required fields are `enabled`,
`bucket`, `region`, `secret_id`, `secret_key`, `prefix` and `storage_class`.
Use `MAZ_STANDARD` for a multi-AZ media bucket with versioning enabled. The file
must be outside source control and readable only by the service identity. A
missing configuration preserves local storage behavior.

New uploads are accepted only after cloud upload and full SHA-256 read-back.
Each local catalog entry and remote recovery pointer records the business key,
size, SHA-256 and fixed COS version ID. Cache misses request that version and
verify the bytes before atomic publication. Metadata and ETag are not used as
proof of original contents. COS or integrity failures return a retryable error.
Existing local originals remain readable during incremental backfill.

Upgrade every process that accesses originals before reclaiming local files.
Backfill must verify database SHA/size, preserve immutable versions and audit all
ZIP entries. Preserve a compatible application rollback image and the current
database; restoring an older database would discard new work.

Keep independent backups in a separate private bucket with separate credentials.
Backup blobs must be independent of the media bucket, encrypted before upload,
and referenced by complete snapshot manifests. Retain seven successful daily
snapshots plus pinned rollback points. Blob retention must compute references
from all retained manifests; a simple seven-day blob expiry is unsafe. Keep
recovery decryption material outside the production host and test restoration
in an isolated environment before local backup reclamation.

`CosStore.prune_cache` requires an independent backup SHA set, seven days of
verified observation, and a one-day last-access grace. It removes only cold,
catalogued local cache files, targeting 2 GiB where these protections permit.
Cloud originals and history are retained. The deployment must arrange a backup
job and call cache maintenance only after its validation gates pass.
