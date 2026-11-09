from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_workflow import pool

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings
from amazon_aigc.domain import active_artist, task_command


@pytest.fixture
def preview_case(db, setup, tmp_path):
    users, item, doc = setup
    task = pool(db, setup)
    with db.connection() as c:
        task_command(c, users['artist'], task['id'], 'claim', 0, {})
        uid = uuid4()
        c.execute("INSERT INTO users(id,name,roles,active,internal,verified_at) VALUES(%s,'Preview Admin',ARRAY['admin'],true,true,now())", (uid,))
        users['admin'] = c.execute('SELECT * FROM users WHERE id=%s', (uid,)).fetchone()
        tokens = {k: new_session(c, u['id']) for k, u in users.items()}
    s = Settings(database_url='postgresql://unused/test', public_url='https://preview.test', web_root=tmp_path)
    client = TestClient(create_app(s, db))
    return users, item, doc, task, client, tokens


def headers(tokens, role, artist=None):
    return {'Authorization': 'Bearer ' + tokens[role], 'Idempotency-Key': str(uuid4()), **({'X-Workbench-Preview': str(artist)} if artist else {})}


def test_preview_keeps_admin_identity_and_enforces_selected_assignee(db, preview_case):
    users, item, _, task, client, tokens = preview_case
    h = headers(tokens, 'admin', users['artist']['id'])
    assert client.get('/api/me', headers=h).json()['user']['id'] == str(users['admin']['id'])
    assert client.get('/api/me', headers=h).json()['user']['roles'] == ['admin']
    assert client.get(f'/api/items/{item}', headers=h).status_code == 200
    assert str(item) in [r['id'] for r in client.get('/api/items', headers=h).json()]
    assert client.get(f'/api/items/{item}', headers=headers(tokens, 'admin', users['art_lead']['id'])).status_code == 404
    for role in ['artist', 'operator', 'art_lead']:
        assert client.get('/api/items', headers=headers(tokens, role, users['artist']['id'])).status_code == 403
    for target in [users['admin']['id'], users['operator']['id'], 'invalid', uuid4()]:
        assert client.get('/api/items', headers=headers(tokens, 'admin', target)).status_code == 403
    with db.connection() as c:
        c.execute('UPDATE artwork_tasks SET assignee_id=%s WHERE id=%s', (users['art_lead']['id'], task['id']))
    assert client.get(f'/api/items/{item}', headers=h).status_code == 404
    with db.connection() as c:
        c.execute('UPDATE users SET active=false WHERE id=%s', (users['art_lead']['id'],))
    assert client.get('/api/items', headers=headers(tokens, 'admin', users['art_lead']['id'])).status_code == 403


def test_preview_blocks_all_writes_and_keeps_real_artist_and_admin_actions(db, preview_case):
    users, item, _, task, client, tokens = preview_case
    h = headers(tokens, 'admin', users['artist']['id'])
    with db.connection() as c:
        before = c.execute('SELECT count(*) n FROM notification_outbox').fetchone()['n']
    for action in ['start', 'return', 'deliver', 'withdraw', 'save-review', 'review', 'cancel-review', 'claim']:
        r = client.post(f"/api/artwork/{task['id']}/{action}", headers=h, json={'revision': 1, 'data': {}})
        assert r.status_code == 403 and '只读' in r.json()['detail']
    assert client.post('/api/artwork/assign', headers=h, json={}).status_code == 403
    assert client.post(f'/api/items/{item}/assets/upload', headers=h, files={'file': ('x.png', b'not used', 'image/png')}).status_code == 403
    assert client.put(f'/api/items/{item}/document', headers=h, json={'revision': 0, 'section': 'facts', 'body': {}}).status_code == 403
    with db.connection() as c:
        assert c.execute('SELECT count(*) n FROM notification_outbox').fetchone()['n'] == before
        assert c.execute('SELECT state FROM artwork_tasks WHERE id=%s', (task['id'],)).fetchone()['state'] == 'waiting'
        assert not active_artist(users['admin'])
    # Removing preview does not turn an admin into the assigned artist.
    assert client.post(f"/api/artwork/{task['id']}/start", headers=headers(tokens, 'admin'), json={'revision': 1, 'data': {}}).status_code == 403
    real = client.post(f"/api/artwork/{task['id']}/start", headers=headers(tokens, 'artist'), json={'revision': 1, 'data': {}})
    assert real.status_code == 200 and real.json()['tasks'][0]['state'] == 'working'
    assert client.get('/api/admin/providers', headers=headers(tokens, 'admin')).status_code == 200


def test_admin_with_artist_role_can_work_only_in_actual_mode(db, preview_case):
    users, _, _, task, client, tokens = preview_case
    with db.connection() as c:
        c.execute("UPDATE users SET roles=ARRAY['admin','artist'] WHERE id=%s", (users['artist']['id'],))
    h = headers(tokens, 'artist', users['art_lead']['id'])
    assert client.post(f"/api/artwork/{task['id']}/start", headers=h, json={'revision': 1, 'data': {}}).status_code == 403
    assert client.post(f"/api/artwork/{task['id']}/start", headers=headers(tokens, 'artist'), json={'revision': 1, 'data': {}}).status_code == 200


def test_preview_does_not_disable_normal_admin_assignment(db, preview_case):
    users, _, _, task, client, tokens = preview_case
    returned = client.post(f"/api/artwork/{task['id']}/return", headers=headers(tokens, 'artist'),
                           json={'revision': 1, 'data': {'reason': 'test'}})
    assert returned.status_code == 200
    body = {'assignee_id': str(users['art_lead']['id']), 'tasks': [{'id': task['id'], 'revision': 2}]}
    assert client.post('/api/artwork/assign', headers=headers(tokens, 'admin', users['artist']['id']), json=body).status_code == 403
    assert client.post('/api/artwork/assign', headers=headers(tokens, 'admin'), json=body).status_code == 200
    people = client.get('/api/people', headers=headers(tokens, 'admin')).json()
    assert not any(p['id'] == str(users['admin']['id']) for p in people)


def test_artist_and_lead_review_visibility_does_not_grant_approval(db, preview_case):
    users, item, _, task, client, tokens = preview_case
    for role in ['artist', 'art_lead']:
        assert client.get(f'/api/items/{item}', headers=headers(tokens, role)).status_code == 200
        for action in ['review', 'save-review', 'cancel-review']:
            r = client.post(f"/api/artwork/{task['id']}/{action}", headers=headers(tokens, role),
                            json={'revision': 1, 'data': {'result': 'accepted'}})
            assert r.status_code == 403
    # A former content owner who now has only an artist role is still read-only.
    with db.connection() as c:
        c.execute('UPDATE work_items SET owner_id=%s WHERE id=%s', (users['art_lead']['id'], item))
    assert client.post(f"/api/artwork/{task['id']}/review", headers=headers(tokens, 'art_lead'),
                       json={'revision': 1, 'data': {'result': 'accepted'}}).status_code == 403


def test_all_artist_preview_aggregates_only_current_active_assignments(db, preview_case, setup):
    from test_unified_artwork import extra_task

    users, item, _, _, client, tokens = preview_case
    other = extra_task(db, setup, 31)
    unassigned = extra_task(db, setup, 32)
    with db.connection() as c:
        task_command(c, users['art_lead'], other['id'], 'claim', 0, {})
        def item_of(tid):
            return c.execute('SELECT d.work_item_id FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE t.id=%s', (tid,)).fetchone()['work_item_id']
        other_item, pool_item = item_of(other['id']), item_of(unassigned['id'])
    h = headers(tokens, 'admin', 'all')
    me = client.get('/api/me', headers=h).json()['user']
    assert me['id'] == str(users['admin']['id']) and me['preview_all_artists']
    ids = {r['id'] for r in client.get('/api/items', headers=h).json()}
    assert {str(item), str(other_item)} <= ids and str(pool_item) not in ids
    assert client.get(f'/api/items/{pool_item}', headers=h).status_code == 404
    assert client.post('/api/artwork/assign', headers=h, json={}).status_code == 403
    assert client.get('/api/items', headers=headers(tokens, 'artist', 'all')).status_code == 403
    with db.connection() as c:c.execute('UPDATE users SET internal=false WHERE id=%s', (users['artist']['id'],))
    assert client.get(f'/api/items/{item}', headers=h).status_code == 404
    assert client.get(f'/api/items/{other_item}', headers=h).status_code == 200
