import io
import os
import tarfile

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.manage import backup, restore
from tests.test_api import HEADERS, PASSWORD, register, course, note, settings_at
from tests.test_auth import token_for


def test_backup_restore_preserves_documents_assets_and_requires_password_recovery(tmp_path):
    source = settings_at(tmp_path / 'source')
    with TestClient(create_app(source), headers=HEADERS) as c:
        register(c); cid = course(c); document = note(c, cid, '## Сохранённый текст\n\n$E=mc^2$')
        image = b'\x89PNG\r\n\x1a\nsynthetic'
        asset = c.post(f"/api/notes/{document['id']}/assets", files={'file': ('x.png', image, 'image/png')}).json()['url']
        target = tmp_path / 'backups' / 'snapshot.tar.gz'
        backup(source, target)
        assert os.stat(target).st_mode & 0o777 == 0o600
        assert c.get('/api/state').status_code == 200  # Snapshot sanitization did not mutate the live DB.
    destination = settings_at(tmp_path / 'restored')
    restore(destination, target)
    with TestClient(create_app(destination), headers=HEADERS) as c:
        assert c.post('/api/auth/login', json={'email': 'author@example.com', 'password': PASSWORD}).status_code == 401
        c.post('/api/auth/forgot-password', json={'email': 'author@example.com'})
        token = token_for(destination, 'reset')
        c.post('/api/auth/reset-password', json={'token': token, 'password': PASSWORD})
        assert c.post('/api/auth/login', json={'email': 'author@example.com', 'password': PASSWORD}).status_code == 200
        assert c.get(f"/api/notes/{document['id']}").json()['markdown'] == '## Сохранённый текст\n\n$E=mc^2$'
        assert c.get(asset).content == image
    with pytest.raises(SystemExit): restore(destination, target)


def test_restore_rejects_traversal_and_links_without_touching_existing_paths(tmp_path):
    for name, link in [('traversal', False), ('link', True)]:
        archive = tmp_path / (name + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as output:
            info = tarfile.TarInfo('../../escape' if not link else 'data/unsafe')
            if link:
                info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'; output.addfile(info)
            else:
                info.size = 1; output.addfile(info, io.BytesIO(b'x'))
        target = settings_at(tmp_path / name)
        with pytest.raises((SystemExit, tarfile.TarError)): restore(target, archive)
        assert not target.data.exists()
    assert not (tmp_path / 'escape').exists()
