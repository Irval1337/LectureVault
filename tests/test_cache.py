from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

from backend import media, worker
from backend.db import connect
from backend.manage import backup, restore
from runner import server
from tests.test_api import course, env, login, note, settings_at

VIDEO = b'0000ftypmp42lecture'
PDF = b'%PDF-1.7 synthetic course slides'
IMAGE = b'\x89PNG\r\n\x1a\nsynthetic illustration'


def submit(client, course_id, language='ru', video=VIDEO, pdf=PDF):
    metadata = dict(courseId=course_id, title='Lecture', language=language, provider='openai', model='test')
    response = client.post('/api/jobs', data={'metadata': json.dumps(metadata)},
                           files={'video': ('lecture.mp4', video), 'pdf': ('slides.pdf', pdf)})
    response.raise_for_status()
    return response.json()['id']


def test_cached_real_media_survives_retry_and_restart_and_respects_acl(env, monkeypatch, tmp_path):
    client, settings, _, _ = env
    cid = course(client)
    monkeypatch.setattr(server, 'JOBS', settings.jobs)
    version = {'speech': 'whisper-a', 'pdf-text': 'poppler-a', 'pdf-jpeg': 'renderer-a'}
    monkeypatch.setattr(server, 'engine_version', version.__getitem__)
    calls = Counter()
    fetched = []
    fail_generation = True

    def parser(argv, *, cwd, read_file=None, on_progress=None, **_kwargs):
        tool = Path(argv[0]).name
        calls[tool] += 1
        assert cwd.parent.parent == settings.jobs
        if read_file:
            # Even when sourceJobId points at the first job, only the input
            # file is readable and all generated output stays in this job.
            assert not read_file.is_relative_to(cwd)
            assert read_file.read_bytes() in (VIDEO, PDF)
        if tool == 'ffprobe':
            return '{"format":{"duration":"60"}}'
        if tool == 'ffmpeg':
            Path(argv[-1]).write_bytes(b'pcm')
        if tool == 'whisper-cli':
            lang = argv[argv.index('-l') + 1]
            on_progress(100)
            Path(argv[-1]).with_suffix('.txt').write_text('Recognized speech ' + lang)
        if tool == 'pdfinfo':
            return 'Pages: 2'
        if tool == 'pdftotext':
            return 'First slide\fSecond slide'
        if tool == 'pdftoppm':
            Path(argv[-1]).with_suffix('.jpg').write_bytes(b'JPEG' + argv[2].encode())
        return ''

    monkeypatch.setattr(server, 'run', parser)

    def rpc(_settings, _service, endpoint, body, **_kwargs):
        nonlocal fail_generation
        if endpoint == '/providers/status':
            return {'providers': [{'id': 'openai', 'connected': True}]}
        if endpoint == '/generate':
            fetched.append((body['transcript'], body['slides']))
            if fail_generation:
                fail_generation = False
                raise worker.JobError('GENERATION_FAILED', 'Retry')
            return {'markdown': '## Lecture notes\n\n' + body['transcript']}
        handlers = {'/transcribe': (server.transcribe, server.MediaInput),
                    '/slides': (server.slides, server.SlidesInput),
                    '/slide-preview': (server.slide_preview, server.SlidePreview)}
        handler, model = handlers[endpoint]
        return handler(model(**body))

    monkeypatch.setattr(worker, 'rpc', rpc)

    def process(job_id):
        row = worker.claim(settings)
        assert row['id'] == job_id
        worker.run_job(settings, row)
        return client.get('/api/jobs/' + job_id).json()

    first_id = submit(client, cid)
    first = process(first_id)
    assert first['errorCode'] == 'GENERATION_FAILED'
    client.post('/api/jobs/' + first_id + '/retry').raise_for_status()
    retried = process(first_id)
    assert retried['status'] == 'ready' and retried['transcriptReused'] and retried['slidesReused']
    second = process(submit(client, cid))
    assert second['transcriptReused'] and second['slidesReused']
    assert calls['whisper-cli'] == calls['pdftotext'] == 1
    assert fetched[0] == fetched[1] == fetched[2] == ('Recognized speech ru', '[Слайд 1] First slide\n[Слайд 2] Second slide')
    assert first['pdfAssetId'] != second['pdfAssetId']
    with connect(settings) as db:
        assert db.execute('SELECT count(DISTINCT path),count(*) FROM assets').fetchone()[:] == (2, 4)
        assert db.execute('SELECT sha256 FROM assets WHERE id=?', (second['sourceAssetId'],)).fetchone()[0] == hashlib.sha256(VIDEO).hexdigest()

    preview = '/api/assets/' + first['pdfAssetId'] + '/slides/1.jpg'
    duplicate_preview = '/api/assets/' + second['pdfAssetId'] + '/slides/1.jpg'
    with ThreadPoolExecutor(max_workers=4) as threads:
        assert list(threads.map(lambda url: client.get(url).content, [preview, duplicate_preview] * 2)) == [b'JPEG1'] * 4
    assert calls['pdftoppm'] == 1
    assert client.get(duplicate_preview.replace('1.jpg', '2.jpg')).content == b'JPEG2'
    assert calls['pdftoppm'] == 2
    assert client.get('/api/assets/' + second['sourceAssetId']).content == VIDEO

    english = process(submit(client, cid, 'en'))
    assert not english['transcriptReused'] and english['slidesReused']
    assert (settings.jobs / english['id'] / 'recognition.progress').read_text() == '100'
    version['speech'] = 'whisper-b'
    assert not process(submit(client, cid))['transcriptReused']
    assert calls['whisper-cli'] == 3 and calls['pdftotext'] == 1

    # The same byte sequence in another owner's private course has a separate result.
    login(client, 'reader')
    assert client.get(duplicate_preview).status_code == 404
    other = course(client)
    separate = process(submit(client, other))
    assert not separate['transcriptReused'] and not separate['slidesReused']
    login(client)
    assert client.get('/api/assets/' + separate['sourceAssetId']).status_code == 404
    assert calls['whisper-cli'] == 4 and calls['pdftotext'] == 2

    archive = tmp_path / 'cache-backup.tar.gz'
    backup(settings, archive)
    restored = settings_at(tmp_path / 'restored')
    restore(restored, archive)
    with connect(restored) as db:
        paths = [Path(row[0]) for row in db.execute('SELECT path FROM assets WHERE course_id=?', (cid,))]
        assert len(set(paths)) == 2 and all(path.is_relative_to(restored.jobs) and path.exists() for path in paths)
    monkeypatch.setattr(server, 'JOBS', restored.jobs)
    result, hit = media.media_result(restored, rpc, second['sourceAssetId'], '/transcribe',
                                     {'jobId': second['id'], 'language': 'ru'}, 180)
    assert hit and result['text'] == 'Recognized speech ru' and calls['whisper-cli'] == 4

    client.delete('/api/courses/' + cid).raise_for_status()
    with connect(settings) as db:
        assert db.execute('SELECT count(*) FROM media_results WHERE course_id=?', (cid,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM media_results WHERE course_id=?', (other,)).fetchone()[0] == 2
    assert not (settings.jobs / first_id).exists()


def test_image_dedup_preserves_legacy_assets_references_and_logical_quota(env):
    client, settings, _, _ = env
    cid = course(client)
    first, second = note(client, cid), note(client, cid)
    def upload(document):
        return client.post('/api/notes/' + document['id'] + '/assets', files={'file': ('image.png', IMAGE)})
    a = upload(first).json()
    with connect(settings) as db:
        db.execute('UPDATE assets SET sha256=NULL')  # An image uploaded before migration.
    b = upload(second).json()
    assert a['id'] != b['id']
    assets = settings.data / 'assets'
    assert len(list(assets.iterdir())) == 1
    settings.user_quota = 2 * len(IMAGE)
    assert upload(first).status_code == 507
    client.delete('/api/notes/' + first['id']).raise_for_status()
    assert client.get(a['url']).status_code == 404 and client.get(b['url']).content == IMAGE
    client.delete('/api/notes/' + second['id']).raise_for_status()
    assert not list(assets.iterdir())


def test_result_eviction_reprocesses_oldest_and_oversize_is_returned(env, monkeypatch):
    client, settings, _, _ = env
    cid = course(client)
    def rpc(_settings, _service, endpoint, body, **_kwargs):
        if endpoint == '/providers/status':
            return {'providers': [{'id': 'openai', 'connected': True}]}
        if body['cachedVersion'] == 'renderer':
            return {'unchanged': True}
        return {'cacheVersion': 'renderer', 'jpeg': 'page-' + str(body['page']), 'pages': 3}
    monkeypatch.setattr(worker, 'rpc', rpc)
    job_id = submit(client, cid)
    pdf = client.get('/api/jobs/' + job_id).json()['pdfAssetId']
    def render(page):
        return media.media_result(settings, rpc, pdf, '/slide-preview', {'jobId': job_id, 'page': page}, 45)
    ticks = iter(range(100))
    monkeypatch.setattr(media, 'now', lambda: next(ticks))
    monkeypatch.setattr(media, 'MAX_CACHE_ENTRIES', 2)
    assert not render(1)[1] and not render(2)[1] and not render(3)[1]
    assert render(2)[1] and not render(1)[1]
    monkeypatch.setattr(media, 'MAX_CACHE_BYTES', 1)
    assert render(3) == ({'jpeg': 'page-3', 'pages': 3}, False)
    assert not render(3)[1]
