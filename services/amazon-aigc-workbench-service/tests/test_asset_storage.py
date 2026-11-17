import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amazon_aigc.asset_storage import CosStore, StorageError, original_exists, safe_path


class Missing(Exception):
    def get_status_code(self):
        return 404


class Body:
    def __init__(self, data):
        self.data = data

    def get_raw_stream(self):
        return io.BytesIO(self.data)


class FakeCos:
    def __init__(self):
        self.objects = {}
        self.latest = {}
        self.corrupt = False

    def head_object(self, Bucket, Key):
        if Key not in self.latest:
            raise Missing()
        value = self.objects[Key, self.latest[Key]]
        return {'Content-Length': str(len(value['data'])), 'x-cos-version-id': self.latest[Key], **value['meta']}

    def get_object(self, Bucket, Key, VersionId=None):
        version = VersionId or self.latest.get(Key)
        if (Key, version) not in self.objects:
            raise Missing()
        data = self.objects[Key, version]['data']
        return {'Body': Body(data + b'corrupt' if self.corrupt and '/_locations/' not in Key else data)}

    def put_object(self, Bucket, Key, Body, **kwargs):
        version = str(len(self.objects) + 1)
        self.objects[Key, version] = {'data': Body, 'meta': kwargs.get('Metadata', {})}
        self.latest[Key] = version
        return {'x-cos-version-id': version}

    def upload_file(self, Bucket, Key, LocalFilePath, **kwargs):
        return self.put_object(Bucket, Key, Path(LocalFilePath).read_bytes(), **kwargs)


@pytest.fixture
def store(tmp_path):
    return CosStore(tmp_path / 'assets', {'bucket': 'synthetic', 'prefix': 'assets/'}, FakeCos())


def test_pinned_version_restores_cache_and_disaster_recovery_catalog(store):
    data = b'original bytes'
    path = store.put_bytes('uploads/synthetic', data)
    pointer = store._index('uploads/synthetic')
    store.client.put_object(Bucket=store.bucket, Key=pointer['object_key'], Body=b'other latest')
    path.unlink()
    store._index_path('uploads/synthetic').unlink()
    assert store.ensure('uploads/synthetic', expected_sha=hashlib.sha256(data).hexdigest()).read_bytes() == data
    assert store._index('uploads/synthetic')['version_id'] == pointer['version_id']


def test_checksum_and_business_reference_fail_closed(store):
    path = store.put_bytes('uploads/synthetic', b'original')
    with pytest.raises(StorageError, match='不可覆盖'):
        store.put_bytes('uploads/synthetic', b'changed')
    assert path.read_bytes() == b'original'
    with pytest.raises(StorageError, match='业务版本'):
        store.ensure('uploads/synthetic', expected_sha='0' * 64)
    path.unlink()
    store.client.corrupt = True
    with pytest.raises(StorageError, match='完整性'):
        store.ensure('uploads/synthetic')
    assert not path.exists()


def test_failed_publication_never_creates_verified_catalog(store):
    store.client.corrupt = True
    with pytest.raises(StorageError, match='回读'):
        store.put_bytes('uploads/synthetic', b'original')
    assert not store._index_path('uploads/synthetic').exists()


def test_concurrent_same_key_never_changes_immutable_original(store):
    def write(data):
        try:
            store.put_bytes('uploads/synthetic', data)
            return data
        except StorageError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        successes = [x for x in pool.map(write, [bytes([n]) for n in range(8)]) if x is not None]
    assert len(successes) == 1
    assert store.ensure('uploads/synthetic').read_bytes() == successes[0]
    assert json.loads(store._index_path('uploads/synthetic').read_text())['sha256'] == hashlib.sha256(successes[0]).hexdigest()


def test_cache_retention_requires_observation_independent_backup_and_access_grace(store):
    path = store.put_bytes('uploads/synthetic', b'original')
    value = store._index('uploads/synthetic')
    backed_up = {value['sha256']}
    assert store.prune_cache(backed_up, max_bytes=0)['removed_files'] == 0
    value.update(verified_at=time.time() - 8 * 86400, last_access=time.time() - 2 * 86400)
    store._save('uploads/synthetic', value)
    assert store.prune_cache(set(), max_bytes=0)['removed_files'] == 0
    assert store.prune_cache(backed_up, max_bytes=0)['removed_files'] == 1
    assert not path.exists()
    assert store.ensure('uploads/synthetic').read_bytes() == b'original'


def test_periodic_presence_checks_leave_evicted_cache_cold(store, monkeypatch):
    from amazon_aigc import asset_storage
    path = store.put_bytes('uploads/synthetic', b'original')
    path.unlink()
    monkeypatch.setattr(asset_storage, 'store_for', lambda root: store)
    assert original_exists(store.root, 'uploads/synthetic')
    assert not path.exists()
    assert not original_exists(store.root, 'uploads/missing')


@pytest.mark.parametrize('fault', ['corrupt', 'missing'])
def test_cache_retention_preserves_original_if_pinned_cloud_version_fails(store, fault):
    path = store.put_bytes('uploads/synthetic', b'original')
    value = store._index('uploads/synthetic')
    value.update(verified_at=time.time()-8*86400, last_access=time.time()-2*86400)
    store._save('uploads/synthetic', value)
    if fault == 'corrupt':
        store.client.corrupt = True
    else:
        del store.client.objects[value['object_key'], value['version_id']]
    with pytest.raises(StorageError):
        store.prune_cache({value['sha256']}, max_bytes=0)
    assert path.read_bytes() == b'original'


@pytest.mark.parametrize('key', ['', '../outside', '/absolute', 'a/../../outside', 'a\\b', '\x00'])
def test_rejects_unsafe_paths(tmp_path, key):
    with pytest.raises(StorageError):
        safe_path(tmp_path, key)
