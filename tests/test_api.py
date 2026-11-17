import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.config import Settings
from backend.db import connect, now
from backend.mailer import cipher
from backend.worker import claim, run_job, recover_interrupted

PASSWORD = 'A long password for LectureVault tests'
HEADERS = {'X-LectureVault': '1'}


def settings_at(path, **kwargs):
    return Settings(data=path / 'data', jobs=path / 'jobs', secrets=path / 'secrets', environment='test', verify_email=False, mock_seconds=0, **kwargs)


def register(c, name='author', password=PASSWORD):
    r = c.post('/api/auth/register', json={'email': name + '@example.com', 'name': name, 'password': password})
    assert r.status_code == 200, r.text
    return c.get('/api/auth').json()['user']


def login(c, name='author', password=PASSWORD):
    r = c.post('/api/auth/login', json={'email': name + '@example.com', 'password': password})
    assert r.status_code == 200, r.text
    return r.json()['user']


@pytest.fixture
def env(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings), headers=HEADERS) as c:
        author = register(c); reader = register(c, 'reader'); login(c)
        yield c, settings, author, reader


def course(c, workspace='personal', title='Курс'):
    response = c.post('/api/courses', json={'title': title, 'workspace': workspace})
    assert response.status_code == 201, response.text
    return response.json()['id']


def note(c, course_id, markdown=''):
    response = c.post('/api/notes', json={'courseId': course_id, 'markdown': markdown})
    assert response.status_code == 201, response.text
    return response.json()


def job(c, course_id, source='https://example.com/video', mode='mock', files=None):
    return c.post('/api/jobs', data={'metadata': json.dumps({'courseId': course_id, 'title': 'Видео', 'sourceType': 'file' if files else 'url', 'sourceName': source, 'mode': mode})}, files=files)


def share(c, cid, role='viewer'):
    result = c.post(f'/api/courses/{cid}/members', json={'email': 'reader@example.com', 'role': role})
    assert result.status_code == 200, result.text


def test_new_registered_account_is_empty_and_demo_login_is_gone(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings), headers=HEADERS) as c:
        assert c.get('/api/auth').json()['user'] is None
        assert c.get('/api/state').status_code == 401
        assert c.post('/api/auth/login', json={'userId': 'alice'}).status_code == 422
        user = register(c)
        assert user['id'] not in ['alice', 'bob'] and len(user['id']) == 36
        assert 'users' not in c.get('/api/auth').json()
        assert c.get('/api/state').json() == {'version': 1, 'courses': [], 'notes': [], 'jobs': []}
        n = note(c, course(c))
        assert n['origin'] == 'manual' and n['jobId'] is None and n['markdown'] == ''
        assert c.get('/api/state').json()['jobs'] == []


def test_password_hashing_login_rotation_logout_and_no_secret_echo(env):
    c, settings, author, _ = env
    with connect(settings) as db:
        encoded = db.execute('SELECT password_hash FROM users WHERE id=?', (author['id'],)).fetchone()[0]
    assert encoded.startswith('$argon2id$') and PASSWORD not in encoded
    old_cookie = c.cookies.get('lv_session')
    r = c.post('/api/auth/login', json={'email': 'author@example.com', 'password': PASSWORD})
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert c.cookies.get('lv_session') != old_cookie
    c.post('/api/auth/logout')
    assert c.get('/api/state').status_code == 401
    wrong = c.post('/api/auth/login', json={'email': 'author@example.com', 'password': 'wrong'})
    missing = c.post('/api/auth/login', json={'email': 'missing@example.com', 'password': 'wrong'})
    assert wrong.status_code == missing.status_code == 401 and wrong.json() == missing.json()
    secret = 'Sensitive-password-' * 30
    invalid = c.post('/api/auth/register', json={'email': 'x@example.com', 'name': 'X', 'password': secret})
    assert invalid.status_code == 422 and secret not in invalid.text and 'input' not in invalid.text


def test_csrf_origin_host_and_private_idor(env):
    c, _, _, _ = env
    cid = course(c); n = note(c, cid)
    assert c.post('/api/courses', json={'title': 'No'}, headers={'X-LectureVault': '0'}).status_code == 403
    for origin in ['https://evil.example', 'http://localhost:9999', 'http://localhost.evil.example:5180']:
        assert c.post('/api/courses', json={'title': 'No'}, headers={'Origin': origin}).status_code == 403
    assert c.get('/api/auth', headers={'Host': 'evil.example'}).status_code == 400
    login(c, 'reader')
    assert c.get('/api/state').json()['courses'] == []
    for url in [f"/api/notes/{n['id']}", f"/api/notes/{n['id']}/export", f'/api/courses/{cid}/members']:
        assert c.get(url).status_code == 404
    assert c.patch(f"/api/notes/{n['id']}", json={'title': 'Attack', 'markdown': '', 'revision': 1}).status_code == 404
    assert c.post('/api/notes', json={'courseId': cid}).status_code == 404
    assert c.delete(f'/api/courses/{cid}').status_code == 404


def test_empty_note_rename_conflict_export_metadata_and_restart(env):
    c, settings, _, _ = env
    cid = course(c); n = note(c, cid)
    markdown = '## Заголовок\n\n$$\nx^2\n$$\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n```py\nprint(1)\n```\n\n![Рисунок](/demo-replication.svg)'
    edit = {'title': 'Новое имя', 'markdown': markdown, 'revision': 1}
    response = c.patch(f"/api/notes/{n['id']}", json=edit)
    assert response.status_code == 200 and response.json()['revision'] == 2
    assert c.patch(f"/api/notes/{n['id']}", json=edit).status_code == 409
    assert c.get(f"/api/notes/{n['id']}/export").text == markdown
    metadata = c.get('/api/state').json()['notes'][0]
    assert metadata['markdown'] == '' and not metadata['contentLoaded']
    with TestClient(create_app(settings), headers=HEADERS) as other:
        login(other)
        assert other.get(f"/api/notes/{n['id']}").json()['markdown'] == markdown
    assert c.patch(f"/api/notes/{n['id']}", json={'title': ' ', 'markdown': '', 'revision': 2}).status_code == 422


def test_real_membership_editor_viewer_and_revocation(env):
    c, _, author, reader = env
    cid = course(c, 'shared'); n = note(c, cid)
    login(c, 'reader')
    assert c.get(f"/api/notes/{n['id']}").status_code == 404
    login(c); share(c, cid, 'editor'); login(c, 'reader')
    assert c.patch(f"/api/notes/{n['id']}", json={'title': 'Правка', 'markdown': 'Текст', 'revision': 1}).status_code == 200
    assert c.put(f"/api/courses/{cid}/members/{author['id']}", json={'role': 'viewer'}).status_code == 403
    assert c.delete(f'/api/courses/{cid}').status_code == 403
    login(c)
    assert c.put(f"/api/courses/{cid}/members/{reader['id']}", json={'role': 'viewer'}).status_code == 200
    login(c, 'reader')
    assert c.get(f"/api/notes/{n['id']}").status_code == 200
    assert c.patch(f"/api/notes/{n['id']}", json={'title': 'Denied', 'markdown': '', 'revision': 2}).status_code == 403
    assert c.post('/api/notes', json={'courseId': cid}).status_code == 403
    assert c.delete(f"/api/notes/{n['id']}").status_code == 403
    login(c); c.put(f"/api/courses/{cid}/members/{reader['id']}", json={'role': 'none'}); login(c, 'reader')
    assert c.get(f"/api/notes/{n['id']}").status_code == 404


def test_membership_requires_shared_course_registered_user_and_owner(env):
    c, _, _, _ = env
    personal = course(c)
    assert c.post(f'/api/courses/{personal}/members', json={'email': 'reader@example.com'}).status_code == 422
    shared = course(c, 'shared')
    assert c.post(f'/api/courses/{shared}/members', json={'email': 'unknown@example.com'}).status_code == 404
    assert c.post(f'/api/courses/{shared}/members', json={'email': None}).status_code == 422
    assert c.post(f'/api/courses/{shared}/members', json={'email': 'reader@example.com', 'role': 'owner'}).status_code == 422
    share(c, shared)
    assert c.post(f'/api/courses/{shared}/members', json={'email': 'reader@example.com'}).status_code == 409


def test_assets_signatures_quota_acl_and_symlinks(env):
    c, settings, _, _ = env
    n = note(c, course(c)); url = f"/api/notes/{n['id']}/assets"
    assert c.post(url, files={'file': ('x.svg', b'<svg/>', 'image/svg+xml')}).status_code == 422
    assert c.post(url, files={'file': ('x.png', b'not png', 'image/png')}).status_code == 422
    image = b'\x89PNG\r\n\x1a\nsynthetic'
    response = c.post(url, files={'file': ('../../x.png', image, 'image/png')})
    assert response.status_code == 201
    asset_url = response.json()['url']; assert c.get(asset_url).content == image
    login(c, 'reader'); assert c.get(asset_url).status_code == 404
    assert c.post(url, files={'file': ('x.png', image, 'image/png')}).status_code == 404
    login(c); settings.user_quota = len(image)
    assert c.post(url, files={'file': ('x.png', image, 'image/png')}).status_code == 507
    with connect(settings) as db:
        path = Path(db.execute('SELECT path FROM assets WHERE id=?', (response.json()['id'],)).fetchone()[0])
    path.unlink(); target = settings.data / 'private.png'; target.write_bytes(b'private'); path.symlink_to(target)
    assert c.get(asset_url).status_code == 404


def test_upload_limits_and_real_url_disabled(env):
    c, settings, _, _ = env
    cid = course(c)
    assert job(c, cid, mode='real').status_code == 422
    assert job(c, cid, 'file:///etc/passwd').status_code == 422
    assert job(c, cid, files={'video': ('x.mp4', b'invalid', 'video/mp4')}).status_code == 422
    header = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 20
    response = job(c, cid, files={'video': ('../../lecture;touch hacked.mp4', header, 'video/mp4')})
    assert response.status_code == 201
    result = c.get('/api/jobs/' + response.json()['id']).json()
    assert result['sourceName'] == 'lecture;touch hacked.mp4' and 'touch' not in result['videoPath']
    settings.max_video = 10
    assert job(c, cid, files={'video': ('x.mp4', header, 'video/mp4')}).status_code == 413


def test_real_preflight_requires_own_provider_and_mock_is_test_only(env, monkeypatch):
    from backend import worker
    c, settings, _, _ = env
    cid = course(c)
    seen = []
    def fake_rpc(*args, **kwargs):
        seen.append(args[3]); return {'providers': [{'id': 'codex', 'connected': False}]}
    monkeypatch.setattr(worker, 'rpc', fake_rpc)
    header = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 20
    result = job(c, cid, mode='real', files={'video': ('x.mp4', header, 'video/mp4')})
    assert result.status_code == 422 and result.json()['detail']['field'] == 'provider'
    assert seen[0]['userId'] == c.get('/api/auth').json()['user']['id']
    assert not list(settings.jobs.iterdir())
    settings.environment = 'development'
    assert job(c, cid).status_code == 422


def test_queue_cancel_retry_interruption_and_no_duplicates(env):
    c, settings, _, _ = env
    cid = course(c)
    first = job(c, cid, 'https://error.example/lecture').json()['id']
    second = job(c, cid).json()['id']
    assert c.post(f'/api/jobs/{second}/cancel').status_code == 200
    assert c.post(f'/api/jobs/{second}/cancel').status_code == 409
    run_job(settings, claim(settings)); assert c.get(f'/api/jobs/{first}').json()['status'] == 'failed'
    c.post(f'/api/jobs/{first}/retry'); claim(settings); recover_interrupted(settings)
    assert c.get(f'/api/jobs/{first}').json()['errorCode'] == 'INTERRUPTED'
    c.post(f'/api/jobs/{first}/retry'); row = claim(settings); run_job(settings, row); run_job(settings, row)
    assert c.get(f'/api/jobs/{first}').json()['status'] == 'ready'
    assert len(c.get('/api/state').json()['notes']) == 1
    assert claim(settings) is None


def test_shared_job_controls_only_submitters_provider(env):
    c, settings, _, _ = env
    cid = course(c, 'shared'); share(c, cid, 'editor')
    jid = job(c, cid, 'https://error.example/lecture').json()['id']
    login(c, 'reader'); assert c.get('/api/jobs/' + jid).status_code == 200
    assert c.post(f'/api/jobs/{jid}/cancel').status_code == 403
    run_job(settings, claim(settings))
    assert c.post(f'/api/jobs/{jid}/retry').status_code == 403


def test_explicit_import_is_private_validated_idempotent_and_preserves_text(env):
    c, _, _, _ = env
    payload = {'courses': [{'id': 'old', 'title': 'Старый курс', 'workspace': 'shared'}], 'notes': [{'courseId': 'old', 'title': 'Текст', 'markdown': '## Текст\n\n$E=mc^2$'}]}
    assert c.post('/api/import', json=payload).json()['imported'] == 1
    assert c.post('/api/import', json={**payload, 'readerTheme': 'dark'}).json()['imported'] == 0
    n = c.get('/api/state').json()['notes'][0]
    assert c.get('/api/notes/' + n['id']).json()['markdown'] == payload['notes'][0]['markdown']
    before = c.get('/api/state').json()
    for data in [{'courses': [None]}, {'courses': [{'id': [], 'title': 'Bad'}]}, {'courses': [{'id': 'x', 'title': ' '}]}]:
        assert c.post('/api/import', json=data).status_code == 422
    assert c.get('/api/state').json() == before
    login(c, 'reader'); assert c.get('/api/notes/' + n['id']).status_code == 404


def test_note_course_and_account_deletion_preserves_other_users_data(env):
    c, settings, _, _ = env
    cid = course(c); n = note(c, cid)
    assert c.delete('/api/notes/' + n['id']).status_code == 200
    assert c.get('/api/notes/' + n['id']).status_code == 404
    note(c, cid); assert c.delete('/api/courses/' + cid).status_code == 200
    shared = course(c, 'shared', 'Общий'); share(c, shared, 'editor')
    login(c, 'reader'); personal = course(c); private_note = note(c, personal)
    login(c); own_note = note(c, shared)
    assert c.request('DELETE', '/api/account', json={'password': 'wrong'}).status_code == 401
    assert c.request('DELETE', '/api/account', json={'password': PASSWORD}).status_code == 200
    assert c.get('/api/state').status_code == 401
    login(c, 'reader'); assert c.get('/api/notes/' + private_note['id']).status_code == 200
    assert c.get('/api/notes/' + own_note['id']).status_code == 404


def test_account_deletion_needs_provider_purge_only_when_connected(env, monkeypatch):
    from backend import worker
    c, settings, author, _ = env
    with connect(settings) as db: db.execute('INSERT INTO provider_links VALUES (?)', (author['id'],))
    def unavailable(*args, **kwargs): raise worker.JobError('UNAVAILABLE', 'Unavailable')
    monkeypatch.setattr(worker, 'rpc', unavailable)
    assert c.request('DELETE', '/api/account', json={'password': PASSWORD}).status_code == 503
    assert c.get('/api/auth').json()['user']['id'] == author['id']
    seen = []
    monkeypatch.setattr(worker, 'rpc', lambda *args, **kwargs: seen.append(args) or {'ok': True})
    assert c.request('DELETE', '/api/account', json={'password': PASSWORD}).status_code == 200
    assert seen[0][2] == '/profiles/purge' and seen[0][3] == {'userId': author['id']}


def test_provider_identity_is_server_owned_and_reauth_required(env, monkeypatch):
    from backend import worker
    c, settings, author, reader = env
    seen = []
    monkeypatch.setattr(worker, 'rpc', lambda *args, **kwargs: seen.append(args[3]) or {'status': 'starting'})
    assert c.post('/api/providers/codex/connect', json={'userId': reader['id']}).status_code == 200
    assert seen[0]['userId'] == author['id']
    with connect(settings) as db: db.execute('UPDATE sessions SET created_at=?', (now() - 16 * 60_000,))
    assert c.post('/api/providers/codex/connect', json={}).status_code == 428
    assert c.post('/api/auth/reauth', json={'password': PASSWORD}).status_code == 200
    assert c.post('/api/providers/codex/connect', json={}).status_code == 200
