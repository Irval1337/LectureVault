from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import wave

import pytest

from backend import media, worker
from backend.db import connect, job_json, now, uid
from backend.manage import backup, deduplicate_storage, restore
from runner import server
from tests.test_api import course, env, login, note, settings_at, share

VIDEO = b'0000ftypmp42lecture'
REMUXED = b'0000ftypmp42same audio in a different container'
DIFFERENT_AUDIO = b'0000ftypmp42different lecture'
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
    version = {'speech': 'a' * 64, 'pdf-text': 'poppler-a'}
    monkeypatch.setattr(server, 'engine_version', version.__getitem__)
    calls = Counter()
    fetched = []
    fail_generation = True

    def parser(argv, *, cwd, read_file=None, on_progress=None, **_kwargs):
        tool = Path(argv[0]).name
        calls[tool] += 1
        if read_file:
            # Only the authorized source is readable from the parser scratch directory.
            assert not read_file.is_relative_to(cwd)
            assert read_file.read_bytes() in (VIDEO, REMUXED, DIFFERENT_AUDIO, PDF)
        if tool == 'ffprobe':
            return '{"format":{"duration":"60"}}'
        if tool == 'ffmpeg':
            assert argv[argv.index('-ac') + 1] == '1' and argv[argv.index('-ar') + 1] == '16000'
            with wave.open(argv[-1], 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x01\x00' if read_file.read_bytes() == DIFFERENT_AUDIO else b'\x00\x01')
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
    source_id = client.get('/api/jobs/' + first_id).json()['sourceAssetId']
    assert client.get('/api/storage').json()['temporary'] == len(VIDEO)
    first = process(first_id)
    assert first['errorCode'] == 'GENERATION_FAILED' and first['canRetry']
    assert 'sourceAssetId' not in first and client.get('/api/assets/' + source_id).status_code == 404
    assert client.get('/api/storage').json()['temporary'] == 0
    assert not list(settings.jobs.rglob('*.mp4'))
    client.post('/api/jobs/' + first_id + '/retry').raise_for_status()
    retried = process(first_id)
    assert retried['status'] == 'ready' and retried['transcriptReused'] and retried['slidesReused']
    second = process(submit(client, cid))
    assert second['transcriptReused'] and second['slidesReused']
    assert calls['whisper-cli'] == calls['pdftotext'] == 1
    assert fetched[0] == fetched[1] == fetched[2] == ('Recognized speech ru', '[Слайд 1] First slide\n\n[Слайд 2] Second slide')
    assert first['pdfAssetId'] != second['pdfAssetId']
    assert first['transcriptAssetId'] != second['transcriptAssetId']
    with connect(settings) as db:
        assert db.execute('SELECT count(DISTINCT path),count(*) FROM assets').fetchone()[:] == (2, 4)
        assert db.execute('SELECT sha256 FROM assets WHERE id=?', (second['transcriptAssetId'],)).fetchone()[0] == hashlib.sha256(b'Recognized speech ru').hexdigest()

    preview = '/api/assets/' + first['pdfAssetId'] + '/slides/1.jpg'
    duplicate_preview = '/api/assets/' + second['pdfAssetId'] + '/slides/1.jpg'
    with ThreadPoolExecutor(max_workers=4) as threads:
        assert list(threads.map(lambda url: client.get(url).content, [preview, duplicate_preview] * 2)) == [b'JPEG1'] * 4
    assert calls['pdftoppm'] == 1
    assert client.get(duplicate_preview.replace('1.jpg', '2.jpg')).content == b'JPEG2'
    assert calls['pdftoppm'] == 2
    transcript_url = '/api/assets/' + second['transcriptAssetId']
    assert client.get(transcript_url).text == 'Recognized speech ru'

    english = process(submit(client, cid, 'en'))
    assert not english['transcriptReused'] and english['slidesReused']
    assert (settings.jobs / english['id'] / 'recognition.progress').read_text() == '100'
    version['speech'] = 'b' * 64
    assert not process(submit(client, cid))['transcriptReused']
    assert calls['whisper-cli'] == 3 and calls['pdftotext'] == 1

    # Another authorized upload of the same PCM gets separate refs with shared physical bytes.
    login(client, 'reader')
    assert client.get(duplicate_preview).status_code == 404
    assert client.get(transcript_url).status_code == 404
    other = course(client)
    separate = process(submit(client, other, video=REMUXED))
    assert not separate['transcriptReused'] and not separate['slidesReused']
    other_preview = '/api/assets/' + separate['pdfAssetId'] + '/slides/1.jpg'
    assert client.get(other_preview).content == b'JPEG1'
    assert calls['whisper-cli'] == 3 and calls['pdftoppm'] == 2
    assert separate['transcriptAssetId'] != second['transcriptAssetId']
    assert process(submit(client, other, video=DIFFERENT_AUDIO))['status'] == 'ready'
    assert calls['whisper-cli'] == 4
    login(client)
    assert client.get('/api/assets/' + separate['transcriptAssetId']).status_code == 404
    assert calls['pdftotext'] == 1
    with connect(settings) as db:
        assert db.execute('SELECT count(*) FROM pdf_text_results').fetchone()[0] == 1
    assert not list(settings.jobs.rglob('recognition.wav'))

    # A backup taken after a restart can capture a deferred cleanup and its hard link.
    leftover = settings.data / 'assets' / 'old-transcript.txt'
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        source = db.execute('SELECT path FROM assets WHERE id=?', (separate['transcriptAssetId'],)).fetchone()[0]
        os.link(source, leftover)
        db.execute('INSERT INTO file_leases VALUES (?,?,?)', (uid(), str(leftover), now() + 60_000))
        media.queue_file_cleanup(db, leftover)
    archive = tmp_path / 'cache-backup.tar.gz'
    backup(settings, archive)
    restored = settings_at(tmp_path / 'restored')
    restore(restored, archive)
    assert not (restored.data / 'assets' / leftover.name).exists()
    with connect(restored) as db:
        paths = [Path(row[0]) for row in db.execute('SELECT path FROM assets WHERE course_id=?', (cid,))]
        assert len(set(paths)) == 3 and all(path.exists() for path in paths)
        assert {path.suffix for path in paths} == {'.pdf', '.txt'}
        restored_job = job_json(db.execute('SELECT * FROM jobs WHERE id=?', (second['id'],)).fetchone())
    monkeypatch.setattr(server, 'JOBS', restored.jobs)
    assert media.prepare_transcript(restored, rpc, restored_job) == 'Recognized speech ru'
    assert calls['whisper-cli'] == 4

    client.delete('/api/courses/' + cid).raise_for_status()
    with connect(settings) as db:
        assert db.execute('SELECT count(*) FROM media_results WHERE course_id=?', (cid,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM media_results WHERE course_id=?', (other,)).fetchone()[0] == 1
    assert not (settings.jobs / first_id).exists()
    login(client, 'reader')
    assert client.get(other_preview).content == b'JPEG1'
    assert client.get('/api/assets/' + separate['transcriptAssetId']).text == 'Recognized speech ru'
    client.delete('/api/courses/' + other).raise_for_status()
    assert not list((settings.jobs / 'blobs').iterdir())
    with connect(settings) as db:
        db.execute('DELETE FROM file_leases WHERE path=?', (str(leftover),))
    media.collect_storage_garbage(settings)
    assert not list((settings.data / 'assets').iterdir())
    with connect(settings) as db:
        assert db.execute('SELECT count(*) FROM speech_results').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM pdf_text_results').fetchone()[0] == 0


def test_image_dedup_preserves_legacy_references_and_owner_quota(env):
    client, settings, _, _ = env
    cid = course(client)
    first, second = note(client, cid), note(client, cid)
    def upload(document, content=IMAGE):
        return client.post('/api/notes/' + document['id'] + '/assets', files={'file': ('image.png', content)})
    a = upload(first).json()
    with connect(settings) as db:
        db.execute('UPDATE assets SET sha256=NULL')  # An image uploaded before migration.
    b = upload(second).json()
    assert a['id'] != b['id']
    assets = settings.data / 'assets'
    assert len(list(assets.iterdir())) == 1
    settings.user_quota = len(IMAGE)
    assert upload(first).status_code == 201
    assert upload(first, IMAGE + b'distinct').status_code == 507
    client.delete('/api/notes/' + first['id']).raise_for_status()
    assert client.get(a['url']).status_code == 404 and client.get(b['url']).content == IMAGE
    client.delete('/api/notes/' + second['id']).raise_for_status()
    assert not list(assets.iterdir())


def test_offline_migration_preserves_legacy_transcript_and_pdf_references(env, capsys):
    client, settings, owner, reader = env
    courses = [course(client)]
    login(client, 'reader')
    courses.append(course(client))
    text = b'Legacy transcript with its exact bytes\n'
    video_digest = hashlib.sha256(VIDEO).hexdigest()
    old_paths = []
    transcript_ids = []
    jobs = []
    with connect(settings) as db:
        # Re-create the preceding schema's misleading transcript hash.
        db.execute('ALTER TABLE assets DROP COLUMN legacy_source_sha256')
        db.execute('''CREATE UNIQUE INDEX transcripts_source ON assets(course_id,sha256,transcript_language)
            WHERE transcript_language IS NOT NULL''')
        for cid, author in zip(courses, (owner, reader)):
            job_id, txt_id, pdf_id = uid(), uid(), uid()
            jobs.append(job_id)
            transcript_ids.append(txt_id)
            txt = settings.data / 'assets' / f'{txt_id}.txt'
            pdf = settings.jobs / job_id / f'{pdf_id}.pdf'
            pdf.parent.mkdir()
            txt.write_bytes(text)
            pdf.write_bytes(PDF)
            old_paths.extend((txt, pdf))
            db.execute('''INSERT INTO assets(id,course_id,name,mime,path,size,sha256,transcript_language,created_by)
                VALUES (?,?,?,'text/plain',?,?,?,'ru',?)''',
                       (txt_id, cid, 'lecture.txt', str(txt), len(text), video_digest, author['id']))
            db.execute('''INSERT INTO assets(id,course_id,job_id,name,mime,path,size,created_by)
                VALUES (?,?,?,?,'application/pdf',?,?,?)''',
                       (pdf_id, cid, job_id, 'slides.pdf', str(pdf), len(PDF), author['id']))
            payload = json.dumps({'transcriptAssetId': txt_id, 'pdfAssetId': pdf_id, 'language': 'ru', 'title': 'Legacy'})
            db.execute('INSERT INTO jobs(id,course_id,created_by,payload,status,created_at) VALUES (?,?,?,?,?,?)',
                       (job_id, cid, author['id'], payload, 'failed', now()))

    deduplicate_storage(settings, dry_run=True)
    assert '2 duplicate files' in capsys.readouterr().out and all(path.exists() for path in old_paths)
    deduplicate_storage(settings)
    deduplicate_storage(settings)
    assert '0 duplicate files' in capsys.readouterr().out and not any(path.exists() for path in old_paths)
    with connect(settings) as db:
        transcripts = db.execute("SELECT id,path,sha256,legacy_source_sha256 FROM assets WHERE mime='text/plain'").fetchall()
        assert {row['id'] for row in transcripts} == set(transcript_ids)
        assert len({row['path'] for row in transcripts}) == 1
        assert {row['sha256'] for row in transcripts} == {hashlib.sha256(text).hexdigest()}
        assert {row['legacy_source_sha256'] for row in transcripts} == {video_digest}
        assert not db.execute('SELECT 1 FROM speech_results').fetchone()
        job = job_json(db.execute('SELECT * FROM jobs WHERE id=?', (jobs[1],)).fetchone())

    def no_runner(*_args, **_kwargs):
        raise AssertionError('Legacy retries read their own stored transcript')

    assert media.prepare_transcript(settings, no_runner, job) == text.decode()
    assert client.get('/api/assets/' + transcript_ids[0]).status_code == 404
    assert client.get('/api/assets/' + transcript_ids[1]).content == text


def test_legacy_pdf_rehome_keeps_a_reader_lease_until_release(env, monkeypatch):
    client, settings, _, _ = env
    cid = course(client)
    monkeypatch.setattr(worker, 'rpc', lambda *_args, **_kwargs: {
        'providers': [{'id': 'openai', 'connected': True}]})
    first_id = submit(client, cid)
    pdf_id = client.get('/api/jobs/' + first_id).json()['pdfAssetId']
    legacy = settings.jobs / first_id / f'{pdf_id}.pdf'
    lease = uid()
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        initial = Path(db.execute('SELECT path FROM assets WHERE id=?', (pdf_id,)).fetchone()[0])
        initial.rename(legacy)
        db.execute('UPDATE assets SET path=?,sha256=NULL WHERE id=?', (str(legacy), pdf_id))
        db.execute('INSERT INTO file_leases VALUES (?,?,?)', (lease, str(legacy), now() + 60_000))

    submit(client, cid)
    with connect(settings) as db:
        assert db.execute('SELECT path FROM assets WHERE id=?', (pdf_id,)).fetchone()[0] == str(initial)
        assert db.execute('SELECT 1 FROM media_garbage WHERE path=?', (str(legacy),)).fetchone()
        assert legacy.read_bytes() == initial.read_bytes() == PDF
        db.execute('DELETE FROM file_leases WHERE id=?', (lease,))
    media.collect_storage_garbage(settings)
    assert not legacy.exists() and initial.read_bytes() == PDF


def test_legacy_video_storage_conversion_and_removal(env, monkeypatch):
    client, settings, _, _ = env
    cid = course(client)
    fail_speech = True

    def rpc(_settings, _service, endpoint, _body, **_kwargs):
        if endpoint == '/providers/status':
            return {'providers': [{'id': 'openai', 'connected': True}]}
        assert endpoint == '/transcribe'
        if fail_speech:
            raise worker.JobError('NO_SPEECH', 'Недостаточно речи.')
        return {'text': 'A durable transcript'}

    monkeypatch.setattr(worker, 'rpc', rpc)
    job_id = submit(client, cid)
    original = client.get('/api/jobs/' + job_id).json()
    source_id = original['sourceAssetId']
    with connect(settings) as db:
        db.execute("""UPDATE jobs SET status='failed',payload=json_remove(payload,'$.temporaryVideo','$.sourceSha256')
            WHERE id=?""", (job_id,))
        db.execute('UPDATE assets SET sha256=NULL WHERE id=?', (source_id,))
    source_url = '/api/assets/' + source_id
    conversion_url = '/api/storage/assets/' + source_id + '/archive'
    assert client.get(source_url).content == VIDEO
    assert any(item['id'] == source_id and item['canArchive'] for item in client.get('/api/storage').json()['items'])
    client.post(conversion_url).raise_for_status()
    assert client.delete('/api/storage/assets/' + source_id).status_code == 409
    assert client.post('/api/jobs/' + job_id + '/retry').status_code == 409
    worker.archive_video(settings, worker.claim_video_archive(settings))
    assert client.get(source_url).content == VIDEO
    assert any(item['id'] == source_id and item['conversion'] == 'failed' for item in client.get('/api/storage').json()['items'])

    fail_speech = False
    client.post(conversion_url).raise_for_status()
    worker.archive_video(settings, worker.claim_video_archive(settings))
    state = client.get('/api/jobs/' + job_id).json()
    assert state['status'] == 'failed' and state['canRetry'] and 'sourceAssetId' not in state
    assert client.get(source_url).status_code == 404
    assert not list(settings.jobs.rglob('*.mp4'))
    transcript = state['transcriptAssetId']
    assert client.get('/api/assets/' + transcript).text == 'A durable transcript'
    with connect(settings) as db:
        state_internal = job_json(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        assert state_internal['sourceSha256'] == hashlib.sha256(VIDEO).hexdigest()

    share(client, cid, 'editor')
    login(client, 'reader')
    assert client.get('/api/storage').json()['items'] == []
    assert client.get('/api/assets/' + transcript).status_code == 200
    assert client.delete('/api/storage/assets/' + transcript).status_code == 403
    login(client)
    client.delete('/api/storage/assets/' + transcript).raise_for_status()
    assert not list((settings.data / 'assets').glob('*.txt'))
    assert not client.get('/api/jobs/' + job_id).json()['canRetry']
    assert client.post('/api/jobs/' + job_id + '/retry').status_code == 409
    assert client.get('/api/storage').json()['used'] == len(PDF)


def test_failed_recognition_and_cancellation_release_temporary_video(env, monkeypatch):
    client, settings, _, _ = env
    cid = course(client)

    def rpc(_settings, _service, endpoint, _body, **_kwargs):
        if endpoint == '/providers/status':
            return {'providers': [{'id': 'openai', 'connected': True}]}
        assert endpoint == '/transcribe'
        return {'text': ''}

    monkeypatch.setattr(worker, 'rpc', rpc)
    cancelled = submit(client, cid)
    client.post('/api/jobs/' + cancelled + '/cancel').raise_for_status()
    assert not list(settings.jobs.rglob('*.mp4'))
    failed = submit(client, cid)
    worker.run_job(settings, worker.claim(settings))
    job = client.get('/api/jobs/' + failed).json()
    assert job['status'] == 'failed' and job['errorCode'] == 'NO_SPEECH' and not job['canRetry']
    assert 'sourceAssetId' not in job and not list(settings.jobs.rglob('*.mp4'))
    assert client.get('/api/storage').json()['temporary'] == 0


@pytest.mark.parametrize('revoked_during', ('normalization', 'recognition'))
def test_revoked_editor_cannot_attach_a_transcript(env, monkeypatch, revoked_during):
    client, settings, _, reader = env
    cid = course(client)
    share(client, cid, 'editor')
    login(client, 'reader')
    calls = []

    def rpc(_settings, _service, endpoint, body, **_kwargs):
        if endpoint == '/providers/status':
            return {'providers': [{'id': 'openai', 'connected': True}]}
        assert endpoint == '/transcribe'
        phase = 'normalization' if body.get('prepareAudio') else 'recognition'
        calls.append(phase)
        if phase == revoked_during:
            login(client)
            client.put(f"/api/courses/{cid}/members/{reader['id']}", json={'role': 'viewer'}).raise_for_status()
        return {'audioSha256': 'c' * 64, 'engineVersion': 'd' * 64, 'text': 'Recognized private speech'}

    monkeypatch.setattr(worker, 'rpc', rpc)
    job_id = submit(client, cid)
    worker.run_job(settings, worker.claim(settings))
    job = client.get('/api/jobs/' + job_id).json()
    assert job['errorCode'] == 'ACCESS_REVOKED' and 'transcriptAssetId' not in job
    assert calls == (['normalization'] if revoked_during == 'normalization' else ['normalization', 'recognition'])
    assert not list(settings.jobs.rglob('*.mp4')) and not list((settings.data / 'assets').glob('*.txt'))


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
    jobs = [submit(client, cid, pdf=PDF + str(page).encode()) for page in range(1, 4)]
    files = [client.get('/api/jobs/' + id).json()['pdfAssetId'] for id in jobs]
    def render(page):
        return media.media_result(settings, rpc, files[page - 1], '/slides', {'jobId': jobs[page - 1], 'page': page}, 45)
    ticks = iter(range(100))
    monkeypatch.setattr(media, 'now', lambda: next(ticks))
    monkeypatch.setattr(media, 'MAX_CACHE_ENTRIES', 2)
    assert not render(1)[1] and not render(2)[1] and not render(3)[1]
    assert render(2)[1] and not render(1)[1]
    monkeypatch.setattr(media, 'MAX_CACHE_BYTES', 1)
    assert render(3) == ({'jpeg': 'page-3', 'pages': 3}, False)
    assert not render(3)[1]
