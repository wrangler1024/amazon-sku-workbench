import asyncio

from amazon_aigc.api import _DownloadResponse


def test_client_disconnect_releases_temporary_archive(tmp_path):
    path = tmp_path / 'download.zip'
    path.write_bytes(b'synthetic archive')
    cleaned = []

    def cleanup():
        path.unlink()
        cleaned.append(True)

    async def receive():
        return {'type': 'http.disconnect'}

    async def send(message):
        if message['type'] == 'http.response.body':
            raise OSError('client disconnected')

    response = _DownloadResponse(path, cleanup=cleanup)
    asyncio.run(response({'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}, receive, send))
    assert cleaned == [True]
    assert not path.exists()
