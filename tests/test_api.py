import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.config import Settings
from backend.db import connect
from backend.worker import claim, run_job


@pytest.fixture
def env(tmp_path):
    settings = Settings(data=tmp_path / 'data', jobs=tmp_path / 'jobs', secrets=tmp_path / 'secrets', mock_seconds=0)
    with TestClient(create_app(settings)) as client:
        client.headers['X-LectureVault'] = '1'
        yield client, settings


def login(c, name='alice'):
    result = c.post('/api/auth/login', json={'userId': name})
    assert result.status_code == 200
    assert 'HttpOnly' in result.headers['set-cookie']


def course(c, workspace='personal'):
    response = c.post('/api/courses', json={'title': 'Курс', 'workspace': workspace})
    assert response.status_code == 201
    return response.json()['id']


def note(c, course_id):
    r = c.post('/api/notes', json={'courseId': course_id})
    assert r.status_code == 201
    return r.json()


def test_auth_csrf_and_private_idor(env):
    c, _ = env
    assert c.get('/api/state').status_code == 401
    assert c.post('/api/auth/login', json={'userId': 'root'}).status_code == 400
    login(c)
    c.headers.pop('X-LectureVault')
    assert c.post('/api/courses', json={'title': 'No'}).status_code == 403
    c.headers['X-LectureVault'] = '1'
    assert c.post('/api/courses', json={'title': 'No'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    personal = course(c)
    n = note(c, personal)
    login(c, 'bob')
    assert personal not in [x['id'] for x in c.get('/api/state').json()['courses']]
    for path in [f"/api/notes/{n['id']}", f"/api/notes/{n['id']}/export", f'/api/courses/{personal}/members']:
        assert c.get(path).status_code == 404
    assert c.patch(f"/api/notes/{n['id']}", json={'title': 'Attack', 'markdown': '', 'revision': 1}).status_code == 404
    assert c.post('/api/notes', json={'courseId': personal}).status_code == 404


def test_empty_note_rename_revision_export_and_restart(env):
    c, settings = env
    login(c)
    n = note(c, course(c))
    assert n['markdown'] == '' and n['origin'] == 'manual' and n['jobId'] is None
    assert c.get('/api/state').json()['jobs'] == []
    assert c.get(f"/api/notes/{n['id']}/export").content == b''
    markdown = '## Заголовок\n\n$$\nx^2\n$$\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n```py\nprint(1)\n```\n\n![Пример](/demo-replication.svg)'
    edit = {'title': 'Новое имя', 'markdown': markdown, 'revision': 1}
    saved = c.patch(f"/api/notes/{n['id']}", json=edit)
    assert saved.status_code == 200 and saved.json()['revision'] == 2
    assert c.patch(f"/api/notes/{n['id']}", json=edit).status_code == 409
    assert c.get(f"/api/notes/{n['id']}/export").text == markdown
    with TestClient(create_app(settings)) as other:
        other.headers['X-LectureVault'] = '1'
        login(other)
        assert other.get(f"/api/notes/{n['id']}").json()['markdown'] == markdown
    assert c.patch(f"/api/notes/{n['id']}", json={'title': ' ', 'markdown': '', 'revision': 2}).status_code == 422


def test_shared_editor_viewer_and_revocation(env):
    c, _ = env
    login(c)
    shared = course(c, 'shared')
    n = note(c, shared)
    login(c, 'bob')
    assert c.patch(f"/api/notes/{n['id']}", json={'title': 'Правка', 'markdown': 'Текст', 'revision': 1}).status_code == 200
    assert c.put(f'/api/courses/{shared}/members/alice', json={'role': 'viewer'}).status_code == 403
    login(c)
    assert c.put(f'/api/courses/{shared}/members/bob', json={'role': 'viewer'}).status_code == 200
    login(c, 'bob')
    assert c.get(f"/api/notes/{n['id']}").status_code == 200
    assert c.patch(f"/api/notes/{n['id']}", json={'title': 'Denied', 'markdown': '', 'revision': 2}).status_code == 403
    assert c.post('/api/notes', json={'courseId': shared}).status_code == 403
    login(c)
    c.put(f'/api/courses/{shared}/members/bob', json={'role': 'none'})
    login(c, 'bob')
    assert c.get(f"/api/notes/{n['id']}").status_code == 404


def test_assets_signature_and_acl(env):
    c, settings = env
    login(c)
    n = note(c, course(c))
    url = f"/api/notes/{n['id']}/assets"
    assert c.post(url, files={'file': ('x.svg', b'<svg></svg>', 'image/svg+xml')}).status_code == 422
    assert c.post(url, files={'file': ('x.png', b'not png', 'image/png')}).status_code == 422
    image = b'\x89PNG\r\n\x1a\nsynthetic'
    response = c.post(url, files={'file': ('../../x.png', image, 'image/png')})
    assert response.status_code == 201
    asset_url = response.json()['url']
    assert c.get(asset_url).content == image
    assert not (settings.data.parent / 'x.png').exists()
    login(c, 'bob')
    assert c.get(asset_url).status_code == 404
    assert c.post(url, files={'file': ('x.png', image, 'image/png')}).status_code == 404


def create_job(c, course_id, source='https://example.com/video', mode='mock', files=None):
    return c.post('/api/jobs', data={'metadata': json.dumps({'courseId': course_id, 'title': 'Видео', 'sourceType': 'file' if files else 'url', 'sourceName': source, 'mode': mode})}, files=files)


def test_queue_cancel_retry_and_persistence(env):
    c, settings = env
    login(c)
    cid = course(c)
    first = create_job(c, cid, 'https://error.example/lecture').json()['id']
    second = create_job(c, cid).json()['id']
    assert c.post(f'/api/jobs/{second}/cancel').status_code == 200
    assert c.post(f'/api/jobs/{second}/cancel').status_code == 409
    run_job(settings, claim(settings))
    assert c.get(f'/api/jobs/{first}').json()['status'] == 'failed'
    assert c.post(f'/api/jobs/{first}/retry').status_code == 200
    run_job(settings, claim(settings))
    result = c.get(f'/api/jobs/{first}').json()
    assert result['status'] == 'ready' and result['attempt'] == 1
    assert claim(settings) is None
    assert len(c.get('/api/state').json()['notes']) == 1
    with connect(settings) as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (first,)).fetchone()
    run_job(settings, row)
    assert len(c.get('/api/state').json()['notes']) == 1
    login(c, 'bob')
    assert c.get(f'/api/jobs/{first}').status_code == 404
    assert c.post(f'/api/jobs/{first}/retry').status_code == 404


def test_upload_limits_safe_filename_and_url_disabled(env):
    c, settings = env
    login(c)
    cid = course(c)
    assert create_job(c, cid, mode='real').status_code == 422
    assert create_job(c, cid, 'file:///etc/passwd').status_code == 422
    assert create_job(c, cid, files={'video': ('x.mp4', b'not video', 'video/mp4')}).status_code == 422
    header = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 20
    response = create_job(c, cid, files={'video': ('../../lecture;touch hacked.mp4', header, 'video/mp4')})
    assert response.status_code == 201
    data = c.get('/api/jobs/' + response.json()['id']).json()
    assert data['sourceName'] == 'lecture;touch hacked.mp4'
    assert (settings.jobs / data['id'] / data['videoPath']).is_file()
    assert 'touch' not in data['videoPath']
    settings.max_video = 10
    assert create_job(c, cid, files={'video': ('x.mp4', header, 'video/mp4')}).status_code == 413


def test_import_is_explicit_idempotent_and_private(env):
    c, _ = env
    login(c)
    payload = {'courses': [{'id': 'old', 'title': 'Старый курс', 'workspace': 'shared'}], 'notes': [{'courseId': 'old', 'title': 'Старый текст', 'markdown': '## Текст\n\n$E=mc^2$'}]}
    assert c.post('/api/import', json=payload).json()['imported'] == 1
    assert c.post('/api/import', json=payload).json()['imported'] == 0
    n = c.get('/api/state').json()['notes'][0]
    assert n['markdown'] == payload['notes'][0]['markdown']
    login(c, 'bob')
    assert c.get(f"/api/notes/{n['id']}").status_code == 404


def test_import_validation_rolls_back_and_theme_does_not_duplicate(env):
    c, _ = env
    login(c)
    before = c.get('/api/state').json()
    for payload in [
        {'courses': [None]},
        {'courses': [{'id': [], 'title': 'Bad'}]},
        {'courses': [{'id': 'x', 'title': ' '}]},
        {'courses': [{'id': 'x', 'title': 'One'}, {'id': 'x', 'title': 'Two'}]},
        {'courses': [{'id': 'x', 'title': 'One'}], 'notes': [{'courseId': 'x', 'title': 'A', 'markdown': 2}]},
    ]:
        assert c.post('/api/import', json=payload).status_code == 422
    assert c.get('/api/state').json() == before
    data = {'courses': [{'id': 'x', 'title': 'Valid'}], 'notes': [], 'readerTheme': 'dark'}
    assert c.post('/api/import', json=data).status_code == 200
    assert c.post('/api/import', json={**data, 'readerTheme': 'light'}).json()['imported'] == 0


def test_worker_interruption_is_recoverable_without_duplicate_note(env):
    from backend.worker import recover_interrupted
    c, settings = env
    login(c)
    first = create_job(c, course(c)).json()['id']
    assert claim(settings)['id'] == first
    recover_interrupted(settings)
    interrupted = c.get(f'/api/jobs/{first}').json()
    assert interrupted['status'] == 'failed' and interrupted['errorCode'] == 'INTERRUPTED'
    assert c.post(f'/api/jobs/{first}/retry').status_code == 200
    run_job(settings, claim(settings))
    recover_interrupted(settings)
    assert c.get(f'/api/jobs/{first}').json()['status'] == 'ready'
    assert len(c.get('/api/state').json()['notes']) == 1


def test_shared_job_can_only_use_its_submitters_provider(env):
    c, settings = env
    login(c)
    job_id = create_job(c, course(c, 'shared'), 'https://error.example/lecture').json()['id']
    login(c, 'bob')
    assert c.get(f'/api/jobs/{job_id}').status_code == 200
    assert c.post(f'/api/jobs/{job_id}/cancel').status_code == 403
    run_job(settings, claim(settings))
    assert c.post(f'/api/jobs/{job_id}/retry').status_code == 403


def test_assets_cannot_follow_symlink(env, tmp_path):
    c, settings = env
    login(c)
    n = note(c, course(c))
    r = c.post(f"/api/notes/{n['id']}/assets", files={'file': ('x.png', b'\x89PNG\r\n\x1a\nsynthetic', 'image/png')})
    with connect(settings) as db:
        asset = db.execute('SELECT * FROM assets WHERE id=?', (r.json()['id'],)).fetchone()
    path = Path(asset['path'])
    path.unlink()
    target = settings.data / 'other.png'
    target.write_bytes(b'private')
    path.symlink_to(target)
    assert c.get(r.json()['url']).status_code == 404


def test_real_job_checks_provider_before_processing_or_retaining_video(env, monkeypatch):
    from backend import worker
    c, settings = env
    login(c)
    cid = course(c)
    monkeypatch.setattr(worker, 'rpc', lambda *_args, **_kwargs: {'providers': [{'id': 'codex', 'connected': False}]})
    header = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 20
    response = create_job(c, cid, mode='real', files={'video': ('x.mp4', header, 'video/mp4')})
    assert response.status_code == 422
    assert response.json()['detail']['field'] == 'provider'
    assert c.get('/api/state').json()['jobs'] == []
    assert list(settings.jobs.iterdir()) == []
