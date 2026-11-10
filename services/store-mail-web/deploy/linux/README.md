# Independent deployment templates

This module has its own HTTP service, SQLite database, configuration and Feishu
application. Adding its code to the workbench does not change any existing service.
Use a new isolated environment for installation validation.

## Containers

From the module root, create a private `.env` from `.env.example` with a generated
Fernet key and administrator password hash. Set `STORE_MAIL_WEB_HOST=0.0.0.0` and
`STORE_MAIL_WEB_DB_PATH=/app/var/store_mail_web.sqlite3` inside the container.

```sh
docker compose config --quiet
docker compose up -d --build
curl http://127.0.0.1:8789/healthz
```

The host port is loopback-only; configure your own HTTPS reverse proxy. The `var`
directory is an explicit persistent mount. The Docker build uses a file allowlist
and locked runtime requirements; it does not copy `.env`, SQLite or email data.

Normal startup runs background workers. Add accounts and enable external services
only as part of an authorized environment setup. For local UI review without
external requests, use `scripts/dashboard_demo.py`, not a real deployment database.

## systemd

`store-mail-web.service` and `store-mail-web.env.example` use generic paths under
`/opt/store-mail-web`, `/etc/store-mail-web` and `/var/lib/store-mail-web`. Adapt
the service account and paths to your installation. No installer is run by CI.
Install a tested wheel into the service virtual environment and supply a private
environment file before explicitly starting the unit.

The `.env.example` at the module root lists all supported variables. Keep private
configuration readable only by the service account and administrators.

## Existing installations and rollback

Do not point development tests at an existing mailbox database. Before any future
deployment, back up SQLite consistently together with its original encryption key,
private configuration and version identifier. Retain mailbox permissions, identities,
read/flag state, sync cursors and notification outbox.

Reverting code does not authorize replacing the database with an old backup: that
would discard newly received messages and user actions. Schema compatibility and
targeted data recovery must be evaluated separately. This repository integration
does not unify login, migrate SQLite to PostgreSQL or modify current mailbox grants.
