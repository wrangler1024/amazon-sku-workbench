"""Immutable COS originals with a verified local cache and stable business keys.

The optional private configuration lives outside the asset root. Existing UUIDs,
database references, authorization and download names are unchanged. Each object
has an immutable version pointer, also persisted in COS for disaster recovery.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath


class StorageError(RuntimeError):
    """Sanitized storage failure, safe to expose as a retryable API response."""


def safe_path(root, key):
    root = Path(root).resolve()
    pure = PurePosixPath(str(key))
    if not str(key) or pure.is_absolute() or '..' in pure.parts or '\\' in str(key) or '\x00' in str(key):
        raise StorageError('无效的图片存储路径')
    path = (root / str(pure)).resolve()
    if not path.is_relative_to(root) or path == root:
        raise StorageError('无效的图片存储路径')
    return path


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.cos-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CosStore:
    def __init__(self, root, config, client=None):
        self.root = Path(root).resolve()
        self.index_root = self.root.parent / 'cos-index'
        self.bucket = config['bucket']
        self.prefix = config.get('prefix', 'assets/').strip('/') + '/'
        self.storage_class = config.get('storage_class', 'MAZ_STANDARD')
        if self.storage_class not in ('MAZ_STANDARD', 'STANDARD'):
            raise StorageError('在线原图必须使用标准存储')
        if client is None:
            from qcloud_cos import CosConfig, CosS3Client
            client = CosS3Client(CosConfig(
                Region=config['region'], SecretId=config['secret_id'], SecretKey=config['secret_key'],
                Token=config.get('token'), Scheme='https', Timeout=20, PoolConnections=8, PoolMaxSize=8,
            ))
        self.client = client

    def _key(self, key):
        safe_path(self.root, key)
        return self.prefix + str(PurePosixPath(str(key)))

    def _index_path(self, key):
        safe_path(self.root, key)
        return self.index_root / (hashlib.sha256(str(key).encode()).hexdigest() + '.json')

    def _pointer_key(self, key):
        return self.prefix + '_locations/' + hashlib.sha256(str(key).encode()).hexdigest() + '.json'

    @contextlib.contextmanager
    def _lock(self, key):
        folder = self.index_root / 'locks'
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / (hashlib.sha256(str(key).encode()).hexdigest() + '.lock')).open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _save(self, key, value):
        atomic_bytes(self._index_path(key), json.dumps(value, sort_keys=True).encode())

    def _read_response(self, response):
        stream = response['Body'].get_raw_stream()
        try:
            return stream.read()
        finally:
            stream.close()

    def _index(self, key, remote=False):
        path = self._index_path(key)
        if path.is_file():
            value = json.loads(path.read_text())
        elif remote:
            try:
                value = json.loads(self._read_response(self.client.get_object(
                    Bucket=self.bucket, Key=self._pointer_key(key),
                )))
            except Exception as exc:  # noqa: BLE001 - sanitize SDK and transport errors
                if getattr(exc, 'get_status_code', lambda: None)() == 404:
                    return None
                raise StorageError('原图云存储暂时不可用，请重试') from None
        else:
            return None
        if (value.get('key') != str(key) or value.get('bucket') != self.bucket or
                value.get('object_key') != self._key(key) or not value.get('version_id') or
                len(value.get('sha256', '')) != 64):
            raise StorageError('原图存储索引校验失败')
        return value

    def publish(self, key, *, expected_sha=None, mime='application/octet-stream'):
        with self._lock(key):
            return self._publish_locked(key, expected_sha=expected_sha, mime=mime)

    def put_bytes(self, key, data, *, mime='application/octet-stream'):
        path = safe_path(self.root, key)
        digest = hashlib.sha256(data).hexdigest()
        with self._lock(key):
            previous = self._index(key, remote=True)
            if previous and previous['sha256'] != digest:
                raise StorageError('已登记的原图不可覆盖')
            atomic_bytes(path, data)
            self._publish_locked(key, expected_sha=digest, mime=mime)
        return path

    def _publish_locked(self, key, *, expected_sha=None, mime='application/octet-stream'):
        path = safe_path(self.root, key)
        digest = file_sha(path)
        size = path.stat().st_size
        if expected_sha and expected_sha != digest:
            raise StorageError('原图完整性校验失败')
        previous = self._index(key, remote=True)
        if previous:
            if (previous['sha256'], previous['size']) != (digest, size):
                raise StorageError('已登记的原图不可覆盖')
            previous.update(last_access=time.time(), local_mtime_ns=path.stat().st_mtime_ns)
            self._save(key, previous)
            return previous
        try:
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            except Exception as exc:
                if getattr(exc, 'get_status_code', lambda: None)() != 404:
                    raise
                head = None
            if head:
                if head.get('x-cos-meta-sha256') != digest or int(head['Content-Length']) != size:
                    raise StorageError('云端原图已存在且内容不一致')
                response = head
            else:
                response = self.client.upload_file(
                    Bucket=self.bucket, Key=self._key(key), LocalFilePath=str(path),
                    PartSize=16, MAXThread=2, EnableMD5=True, StorageClass=self.storage_class,
                    ContentType=mime, Metadata={'x-cos-meta-sha256': digest},
                )
            version = response.get('x-cos-version-id')
            if not version or version == 'null':
                raise StorageError('原图云存储未启用版本保护')
            # Full read-back SHA verification; ETag and user metadata are not sufficient.
            remote = self.client.get_object(Bucket=self.bucket, Key=self._key(key), VersionId=version)
            stream = remote['Body'].get_raw_stream()
            check = hashlib.sha256()
            count = 0
            try:
                for data in iter(lambda: stream.read(1024 * 1024), b''):
                    check.update(data)
                    count += len(data)
            finally:
                stream.close()
            if check.hexdigest() != digest or count != size:
                raise StorageError('云端原图回读校验失败')
            value = {'schema': 1, 'key': str(key), 'bucket': self.bucket, 'object_key': self._key(key),
                     'version_id': version, 'sha256': digest, 'size': size, 'verified_at': time.time(),
                     'last_access': time.time(), 'local_mtime_ns': path.stat().st_mtime_ns}
            self.client.put_object(Bucket=self.bucket, Key=self._pointer_key(key),
                                   Body=json.dumps(value, sort_keys=True).encode(),
                                   StorageClass=self.storage_class, ContentType='application/json', EnableMD5=True)
            self._save(key, value)
            return value
        except StorageError:
            raise
        except Exception:  # noqa: BLE001 - never expose credentials from SDK errors
            raise StorageError('原图云存储暂时不可用，请重试') from None

    def ensure(self, key, *, expected_sha=None, expected_size=None):
        path = safe_path(self.root, key)
        with self._lock(key):
            value = self._index(key, remote=not path.is_file())
            if value and ((expected_sha and value['sha256'] != expected_sha) or
                          (expected_size is not None and value['size'] != expected_size)):
                raise StorageError('原图存储索引与业务版本不一致')
            if path.is_file():
                if value:
                    if (path.stat().st_size != value['size'] or
                            (path.stat().st_mtime_ns != value.get('local_mtime_ns') and file_sha(path) != value['sha256'])):
                        raise StorageError('本地原图完整性校验失败')
                    value.update(last_access=time.time(), local_mtime_ns=path.stat().st_mtime_ns)
                    self._save(key, value)
                elif ((expected_size is not None and path.stat().st_size != expected_size) or
                      (expected_sha and file_sha(path) != expected_sha)):
                    raise StorageError('本地原图完整性校验失败')
                return path
            if not value:
                raise FileNotFoundError(str(key))
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.cos-', dir=path.parent)
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=value['object_key'], VersionId=value['version_id'])
                stream = response['Body'].get_raw_stream()
                check = hashlib.sha256()
                count = 0
                try:
                    with os.fdopen(fd, 'wb') as out:
                        fd = None
                        for data in iter(lambda: stream.read(1024 * 1024), b''):
                            check.update(data)
                            count += len(data)
                            out.write(data)
                finally:
                    stream.close()
                if count != value['size'] or check.hexdigest() != value['sha256']:
                    raise StorageError('云端原图完整性校验失败')
                os.replace(temporary, path)
                value.update(last_access=time.time(), local_mtime_ns=path.stat().st_mtime_ns)
                self._save(key, value)
                return path
            except StorageError:
                raise
            except Exception:  # noqa: BLE001 - never expose credentials from SDK errors
                raise StorageError('原图云存储暂时不可用，请重试') from None
            finally:
                if fd is not None:
                    os.close(fd)
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def prune_cache(self, backup_shas, *, max_bytes=2 * 1024**3, min_age=7 * 86400, access_grace=86400):
        """Remove verified, independently backed up cold cache files after observation.

        The access grace protects in-flight FileResponses and workers. This method
        never deletes cloud versions, location pointers, or uncatalogued originals.
        """
        now = time.time()
        entries = []
        total = 0
        for index in self.index_root.glob('*.json'):
            value = json.loads(index.read_text())
            path = safe_path(self.root, value['key'])
            if path.is_file():
                total += path.stat().st_size
                entries.append(value)
        removed = count = 0
        for value in sorted(entries, key=lambda v: v.get('last_access', now)):
            if total <= max_bytes:
                break
            if (value['sha256'] not in backup_shas or now - value['verified_at'] < min_age or
                    now - value.get('last_access', now) < access_grace):
                continue
            key = value['key']
            with self._lock(key):
                current = self._index(key)
                if (not current or current['version_id'] != value['version_id'] or
                        now - current.get('last_access', now) < access_grace):
                    continue
                path = safe_path(self.root, key)
                if not path.is_file():
                    continue
                size = path.stat().st_size
                if size != current['size'] or file_sha(path) != current['sha256']:
                    raise StorageError('缓存回收前原图校验失败')
                # Revalidate the fixed cloud version immediately before eviction.
                # An old catalog or successful backup alone cannot prove it is readable now.
                try:
                    remote = self.client.get_object(Bucket=self.bucket, Key=current['object_key'],
                                                    VersionId=current['version_id'])
                    stream = remote['Body'].get_raw_stream()
                    digest = hashlib.sha256()
                    remote_size = 0
                    try:
                        for data in iter(lambda stream=stream: stream.read(1024 * 1024), b''):
                            digest.update(data)
                            remote_size += len(data)
                    finally:
                        stream.close()
                    if remote_size != size or digest.hexdigest() != current['sha256']:
                        raise StorageError('缓存回收前云端原图校验失败')
                except StorageError:
                    raise
                except Exception:  # noqa: BLE001 - sanitize SDK and transport errors
                    raise StorageError('云端原图暂时不可用，保留本地副本') from None
                path.unlink()
                total -= size
                removed += size
                count += 1
        return {'removed_files': count, 'removed_bytes': removed, 'remaining_bytes': total}


@lru_cache(maxsize=8)
def _store(root, config_path, mtime):
    config = json.loads(Path(config_path).read_text())
    return CosStore(root, config) if config.get('enabled') else None


def store_for(root):
    root = Path(root).resolve()
    path = Path(os.environ.get('AIGC_COS_CONFIG_FILE', root.parent / 'private' / 'cos-media.json'))
    if not path.is_file():
        return None
    return _store(str(root), str(path), path.stat().st_mtime_ns)


def file_path(root, key, *, expected_sha=None, expected_size=None):
    store = store_for(root)
    if store:
        return store.ensure(key, expected_sha=expected_sha, expected_size=expected_size)
    return safe_path(root, key)


def original_exists(root, key):
    """Check availability without warming the cache during periodic sync scans."""
    if safe_path(root, key).is_file():
        return True
    store = store_for(root)
    return bool(store and store._index(key))


def asset_path(root, asset):
    digest = asset.get('sha256')
    return file_path(root, asset['object_key'], expected_sha=digest if digest != 'pending' else None,
                     expected_size=asset.get('size_bytes'))


def publish_file(root, key, *, expected_sha=None, mime='application/octet-stream'):
    store = store_for(root)
    if store:
        return store.publish(key, expected_sha=expected_sha, mime=mime)
    return None


def publish_bytes(root, key, data, *, mime='application/octet-stream'):
    path = safe_path(root, key)
    store = store_for(root)
    if store:
        return store.put_bytes(key, data, mime=mime)
    atomic_bytes(path, data)
    return path
