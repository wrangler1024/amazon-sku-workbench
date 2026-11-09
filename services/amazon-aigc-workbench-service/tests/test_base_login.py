import hashlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.base_login import EMBED_COOKIE, FIELDS, TABLE, exchange, issue
from amazon_aigc.config import TEST_BASE, Settings


class Gateway:
    def __init__(self, record):
        self.record = record
        self.fields = [{"field_name": k, "field_id": v[0], "type": v[1]} for k, v in FIELDS.items()]
        self.calls = []

    def request(self, method, path, **kwargs):
        assert path.startswith(f"/bitable/v1/apps/{TEST_BASE}/tables/{TABLE}/")
        self.calls.append((method, path))
        if method == "DELETE":
            return {}
        if path.endswith("/fields"):
            return {"items": self.fields}
        return {"record": self.record}


@pytest.fixture
def login_case(db, setup, tmp_path):
    users, _item, _ = setup
    actor = "on_" + str(users["operator"]["id"]).replace("-", "")
    with db.connection() as c:
        c.execute(
            "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu_union','example',%s,%s)",
            (actor, users["operator"]["id"]),
        )
    challenge = issue(db)
    with db.connection() as c:
        issued = c.execute(
            "SELECT issued_at FROM base_login_challenges WHERE id=%s", (challenge["challenge_id"],)
        ).fetchone()["issued_at"]
    stamp = int(issued.timestamp() * 1000)
    gateway = Gateway(
        {
            "fields": {
                "校验摘要": challenge["proof"],
                "创建人": {"id": actor},
                "更新人": {"id": actor},
                "创建时间": stamp,
                "更新时间": stamp,
            }
        }
    )
    settings = Settings(
        database_url="postgresql://unused/test",
        public_url="https://workbench.test",
        base_login_enabled=True,
        web_root=tmp_path,
    )
    payload = {k: challenge[k] for k in ["challenge_id", "secret"]} | {"record_id": "recProof123456"}
    return users, settings, gateway, payload


def test_server_uses_feishu_creator_and_rejects_replay(db, login_case):
    users, settings, gateway, payload = login_case
    token = exchange(db, settings, {**payload, "user_id": str(users["artist"]["id"]), "roles": ["admin"]}, gateway)
    with db.connection() as c:
        session = c.execute(
            "SELECT user_id FROM sessions WHERE token_hash=%s", (hashlib.sha256(token.encode()).hexdigest(),)
        ).fetchone()
        assert session["user_id"] == users["operator"]["id"]
    assert gateway.calls[-1][0] == "DELETE"
    with pytest.raises(HTTPException, match="失效"):
        exchange(db, settings, payload, gateway)


@pytest.mark.parametrize(
    "change", ["secret", "proof", "creator", "modified_time", "old_record", "schema", "inactive", "external", "expired"]
)
def test_untrusted_or_expired_proofs_never_create_sessions(db, login_case, change):
    users, settings, gateway, payload = login_case
    before = 0
    with db.connection() as c:
        before = c.execute("SELECT count(*) AS n FROM sessions").fetchone()["n"]
        if change in ("inactive", "external"):
            c.execute(
                "UPDATE users SET active=%s,internal=%s WHERE id=%s",
                (change != "inactive", change != "external", users["operator"]["id"]),
            )
        if change == "expired":
            c.execute(
                "UPDATE base_login_challenges SET expires_at=now()-interval '1 minute' WHERE id=%s",
                (payload["challenge_id"],),
            )
    if change == "secret":
        payload["secret"] = "x" * 48
    if change == "proof":
        gateway.record["fields"]["校验摘要"] = "0" * 64
    if change == "creator":
        gateway.record["fields"]["更新人"] = {"id": "on_attacker"}
    if change == "modified_time":
        gateway.record["fields"]["更新时间"] += 1000
    if change == "old_record":
        gateway.record["fields"]["创建时间"] -= 60000
        gateway.record["fields"]["更新时间"] = gateway.record["fields"]["创建时间"]
    if change == "schema":
        gateway.fields[1]["type"] = 11
    with pytest.raises(HTTPException):
        exchange(db, settings, payload, gateway)
    with db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM sessions").fetchone()["n"] == before


def test_host_cookie_origin_and_existing_permissions(db, login_case, setup):
    users, settings, gateway, payload = login_case
    app = create_app(settings, db)
    app.state.base_login_gateway = gateway
    client = TestClient(app, base_url=settings.public_url)
    headers = {"Origin": settings.public_url, "X-Requested-With": "amazon-workbench"}
    assert client.post("/auth/base/exchange", json=payload).status_code == 403
    r = client.post("/auth/base/exchange", json=payload, headers=headers)
    assert r.status_code == 200
    for flag in ["Secure", "HttpOnly", "SameSite=None", "Partitioned", "Path=/"]:
        assert flag in r.headers["set-cookie"]
    # An old independent-web session must not override the current Base identity.
    with db.connection() as c:
        other = new_session(c, users["artist"]["id"])
    client.cookies.set("aigc_session", other)
    current = client.get("/api/me", headers={"X-Workbench-Context": "feishu-base"})
    assert current.json()["user"]["id"] == str(users["operator"]["id"])
    assert client.get("/api/admin/providers").status_code == 403
    client.cookies.delete(EMBED_COOKIE)
    assert client.get("/api/me", headers={"X-Workbench-Context": "feishu-base"}).status_code == 401


def test_identity_disabled_by_default(db, tmp_path):
    s = Settings(database_url="postgresql://unused/test", public_url="https://workbench.test", web_root=tmp_path)
    client = TestClient(create_app(s, db), base_url=s.public_url)
    assert (
        client.post(
            "/auth/base/challenge", headers={"Origin": s.public_url, "X-Requested-With": "amazon-workbench"}
        ).status_code
        == 403
    )


def test_native_plugin_exchange_bearer_and_exact_cors(db, login_case):
    users, settings, gateway, payload = login_case
    app = create_app(settings, db)
    app.state.base_login_gateway = gateway
    client = TestClient(app)
    origin = settings.base_parent_origin
    headers = {'Origin': origin, 'X-Requested-With': 'amazon-workbench'}
    preflight = client.options('/api/items', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'PUT',
        'Access-Control-Request-Headers': 'authorization,content-type,idempotency-key,x-requested-with,x-workbench-context'})
    assert preflight.status_code == 200
    assert preflight.headers['access-control-allow-origin'] == origin
    bad = client.options('/api/items', headers={'Origin': 'https://fake.feishupkg.com', 'Access-Control-Request-Method': 'GET'})
    assert bad.status_code == 400 and 'access-control-allow-origin' not in bad.headers
    result = client.post('/auth/base/exchange', json=payload, headers=headers)
    assert result.status_code == 200 and 'set-cookie' not in result.headers
    assert result.headers['access-control-allow-origin'] == origin
    token = result.json()['access_token']
    identity = client.get('/api/me', headers={**headers, 'Authorization': 'Bearer ' + token})
    assert identity.status_code == 200 and identity.json()['user']['id'] == str(users['operator']['id'])
    assert identity.json()['user']['roles'] == ['operator']
    assert client.post('/auth/base/exchange', json=payload, headers=headers).status_code == 401
    assert client.post('/auth/base/challenge', headers={**headers, 'Origin': 'https://fake.feishupkg.com'}).status_code == 403
    client.post('/auth/logout', headers={**headers, 'Authorization': 'Bearer ' + token})
    assert client.get('/api/me', headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_login_transport_reuses_token_and_reads_fresh_schema_in_parallel(login_case):
    import threading

    import httpx

    from amazon_aigc.base_login import BaseLoginGateway

    _, settings, fake, _ = login_case
    settings.feishu_app_id = 'test-app'
    settings.feishu_app_secret = 'test-only'
    paired = threading.Barrier(2)
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if 'tenant_access_token' in request.url.path:
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'test-token', 'expire': 7200})
        assert request.headers['authorization'] == 'Bearer test-token'
        paired.wait(timeout=3)  # Sequential reads or serialized token/network work fail.
        data = {'items': fake.fields} if request.url.path.endswith('/fields') else {'record': fake.record}
        return httpx.Response(200, json={'code': 0, 'data': data})

    gateway = BaseLoginGateway(settings, httpx.Client(transport=httpx.MockTransport(handle)))
    prefix = f'/bitable/v1/apps/{TEST_BASE}/tables/{TABLE}'
    try:
        first, record = gateway.read_proof(prefix, 'recProof123456')
        assert record == fake.record
        fake.fields = [{'field_name': 'changed', 'field_id': 'different', 'type': 1}]
        second, _ = gateway.read_proof(prefix, 'recProof123456')
        assert first != second  # Never reuse a previously valid schema.
        assert sum('tenant_access_token' in p for p in seen) == 1
        assert sum(p.endswith('/fields') for p in seen) == 2
        with pytest.raises(ValueError, match='Base target refused'):
            gateway.request('GET', '/bitable/v1/apps/other/tables/any/fields')
    finally:
        gateway.close()
    assert gateway.client.is_closed


def test_parallel_transport_cannot_bypass_proof_validation(db, login_case):
    import httpx

    from amazon_aigc.base_login import BaseLoginGateway

    _, settings, fake, payload = login_case
    gateway = BaseLoginGateway(settings, httpx.Client())
    gateway.request = fake.request
    fake.fields[1]['type'] = 11
    with db.connection() as c:
        before = c.execute('SELECT count(*) AS n FROM sessions').fetchone()['n']
    try:
        with pytest.raises(HTTPException, match='表结构不匹配'):
            exchange(db, settings, payload, gateway)
        with db.connection() as c:
            assert c.execute('SELECT count(*) AS n FROM sessions').fetchone()['n'] == before
    finally:
        gateway.close()


def test_cleanup_can_follow_response_without_delaying_identity_or_allowing_replay(db, login_case):
    users, settings, gateway, payload = login_case
    pending = []
    token = exchange(db, settings, payload, gateway, pending.append)
    assert token and len(pending) == 1
    assert all(method == 'GET' for method, _ in gateway.calls)
    with db.connection() as c:
        assert c.execute('SELECT used_at,user_id FROM base_login_challenges WHERE id=%s',
                         (payload['challenge_id'],)).fetchone()['user_id'] == users['operator']['id']
    with pytest.raises(HTTPException, match='失效'):
        exchange(db, settings, payload, gateway, pending.append)
    assert len(pending) == 1
    pending[0]()
    assert gateway.calls[-1][0] == 'DELETE'


def test_temporary_authoritative_read_failure_can_retry_same_proof_without_session(db, login_case):
    _users, settings, gateway, payload = login_case
    original = gateway.request
    failed = False

    def transient(method, path, **kwargs):
        nonlocal failed
        if path.endswith('/fields') and not failed:
            failed = True
            raise RuntimeError('temporary upstream read failure')
        return original(method, path, **kwargs)

    gateway.request = transient
    with db.connection() as c:
        before = c.execute('SELECT count(*) n FROM sessions').fetchone()['n']
    with pytest.raises(HTTPException) as error:
        exchange(db, settings, payload, gateway)
    assert error.value.status_code == 503
    with db.connection() as c:
        assert c.execute('SELECT count(*) n FROM sessions').fetchone()['n'] == before
        assert c.execute('SELECT used_at FROM base_login_challenges WHERE id=%s', (payload['challenge_id'],)).fetchone()['used_at'] is None
    assert exchange(db, settings, payload, gateway)
    with db.connection() as c:
        assert c.execute('SELECT count(*) n FROM sessions').fetchone()['n'] == before + 1
    with pytest.raises(HTTPException):
        exchange(db, settings, payload, gateway)
