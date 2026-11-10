"""Smoke-check an installed wheel using temporary data and loopback HTTP only."""
import json
from pathlib import Path
import tempfile
import urllib.request

import store_mail_web
from store_mail_web.config import Settings
from store_mail_web.crypto import generate_master_key
from store_mail_web.http_api import start_http_server
from store_mail_web.service import Service


def main():
    package = Path(store_mail_web.__file__).resolve().parent
    assert 'site-packages' in package.parts, 'Run this check after installing a wheel in a fresh venv'
    for name in ('index.html', 'app.js', 'roles.js', 'style.css', 'xynigo-logo.png'):
        assert (package / 'static' / name).is_file(), name
    assert (package / 'openapi.yaml').is_file()
    with tempfile.TemporaryDirectory(prefix='mail-installed-check-') as tmp:
        service = Service.build(Settings(
            secret_key=generate_master_key(), admin_password='temporary-test-only',
            db_path=Path(tmp) / 'test.sqlite3', mark_seen_on_open=False,
        ))
        server = start_http_server('127.0.0.1', 0, service, package / 'static', package / 'openapi.yaml')
        try:
            origin = 'http://127.0.0.1:' + str(server.server_address[1])
            for route in ('/healthz', '/', '/static/app.js', '/static/xynigo-logo.png', '/api/openapi.yaml'):
                with urllib.request.urlopen(origin + route, timeout=5) as response:
                    assert response.status == 200, route
                    assert response.read(), route
            assert not service.store.list_accounts()
            assert service.store.message_count() == 0
            print(json.dumps({'version': store_mail_web.__version__, 'installed_wheel': 'passed',
                              'http_routes': 5, 'mailboxes': 0, 'messages': 0}))
        finally:
            server.shutdown()
            server.server_close()
            service.store.close()


if __name__ == '__main__':
    main()
