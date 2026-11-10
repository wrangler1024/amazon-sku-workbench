# Linux deployment

Node.js 22.14+ is required. The templates use `/opt/fba-fee-calculator`, a dedicated
`fba-calculator` user and loopback port8791. Adapt the public hostname, Nginx paths
and certificate paths to your host. No server credentials are included.

## Release archive

From the repository root:

```bash
npm run check
npm test
mkdir -p .local/releases
COPYFILE_DISABLE=1 tar -czf .local/releases/fbacc.tar.gz \
  package.json src rates public tests examples deploy/linux \
  scripts/check-rates.mjs scripts/check-deployed.mjs
shasum -a 256 .local/releases/fbacc.tar.gz
```

Transfer the archive and `install-release.sh` to your own host, then run:

```bash
sudo bash install-release.sh /tmp/fbacc.tar.gz RELEASE_ID SHA256
```

The installer verifies the checksum and Node version, runs the rate validator
and tests as the service user, atomically switches `current`, restarts only this
service and checks `/readyz`. If readiness fails, it restores an existing valid
previous release. On a first installation with no valid previous release, it
stops the service. Existing environment configuration is preserved.

Configuration lives at `/etc/fba-fee-calculator/service.env`; see the example.
Keep secrets out of Git and browser code. The public calculator uses an empty
API key; a private backend can require bearer authentication.

## HTTPS and operations

Adapt `fbacc.http.conf` for ACME validation, obtain a certificate using your
existing certificate-management process, then adapt `fbacc.https.conf` for HTTPS.
Validate and reload the Nginx installation actually serving your host. The
bundled templates use BT Nginx paths; standard installations use different paths.
Configure certificate renewal and its Nginx reload hook. Install the logrotate
example after adapting log paths.

```bash
sudo systemctl status fba-fee-calculator --no-pager
curl --fail http://127.0.0.1:8791/readyz
sudo journalctl -u fba-fee-calculator -n 50 --no-pager
node scripts/check-deployed.mjs https://YOUR_HOST
```

The example gateway limits `/v1/` to8 requests/second per IP with a burst of24,
returns429 with `Retry-After: 2`, and limits request bodies to128KiB.

## Rollback

Use a previously verified release under `/opt/fba-fee-calculator/releases/`.
Inspect `backups/previous-RELEASE_ID.txt`, atomically restore the `current` symlink,
restore the corresponding backed-up systemd unit if it changed, restart only
`fba-fee-calculator`, then check local readiness and the public endpoint.
Do not use an unverified or failed release as a rollback target.

## Optional official-source monitoring

See [monitoring and approval](../../infra/rates/README.md). The monitor is installed
separately and does not automatically publish rates. Its installer does not
enable the timer; operators explicitly choose manual checks or scheduled checks.
