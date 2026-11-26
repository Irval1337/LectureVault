"""Course-owned asset references and shared immutable media bytes."""
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import NamedTuple

from .db import connect, now, uid
from .transcript import AUDIO_NORMALIZATION_TIMEOUT, SPEECH_RECOGNITION_TIMEOUT, clean_transcript

MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_ENTRIES = 2048
MAX_TRANSCRIPT_CHARS = 1_000_000
MAX_TRANSCRIPT_BYTES = MAX_TRANSCRIPT_CHARS * 4
TEMPORARY_VIDEO = "a.mime LIKE 'video/%' AND coalesce(json_extract(j.payload,'$.temporaryVideo'),0)=1"
BLOB_SUFFIXES = {'application/pdf': '.pdf', 'image/jpeg': '.jpg', 'image/png': '.png',
                 'image/webp': '.webp', 'text/plain': '.txt'}
_result_locks = [threading.Lock() for _ in range(64)]
log = logging.getLogger(__name__)


class Upload(NamedTuple):
    id: str
    name: str
    mime: str
    path: str
    size: int
    sha256: str


def regular_upload(path, root):
    return (path.is_relative_to(root) and '..' not in path.parts
            and not any(part.is_symlink() for part in (path, *path.parents)) and path.is_file())


def file_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def index_matching_legacy_uploads(settings, _course_id, upload, root):
    # Limit lazy migration to same-size, same-format candidates.
    # File I/O happens outside SQLite write transactions.
    with connect(settings) as db:
        candidates = db.execute("""SELECT DISTINCT path FROM assets WHERE mime=? AND size=?
            AND sha256 IS NULL""", (upload.mime, upload.size)).fetchall()
    for row in candidates:
        path = Path(row['path'])
        if not regular_upload(path, root):
            continue
        try:
            with path.open('rb') as file:
                stat = os.fstat(file.fileno())
                if stat.st_size != upload.size:
                    continue
                identity = file_identity(stat)
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
        except FileNotFoundError:
            continue
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if file_identity(path.stat()) != identity:
                    continue
            except FileNotFoundError:
                continue
            db.execute('UPDATE assets SET sha256=? WHERE path=? AND mime=? AND size=? AND sha256 IS NULL',
                       (digest, str(path), upload.mime, upload.size))


def blob_path(settings, item):
    suffix = BLOB_SUFFIXES[item.mime]
    directory = settings.jobs / 'blobs' if item.mime == 'application/pdf' else settings.data / 'assets'
    return directory / f'{item.sha256}{suffix}'


def materialize_blob(db, settings, item):
    """Install one durable copy and move indexed legacy references to it."""
    path = blob_path(settings, item)
    if not regular_upload(path, path.parent):
        source = Path(item.path)
        root = settings.jobs if item.mime == 'application/pdf' else settings.data / 'assets'
        if not regular_upload(source, root) or any(parent.is_symlink() for parent in path.parents):
            raise OSError('Unsafe asset file')
        with source.open('rb') as file:
            os.fsync(file.fileno())
        os.link(source, path, follow_symlinks=False)
        db.uncommitted_files.add(str(path))
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if path.stat().st_size != item.size:
        raise OSError('Asset size does not match its identity')
    matching = (item.sha256, item.mime, item.size, str(path))
    old_paths = db.execute('SELECT DISTINCT path FROM assets WHERE sha256=? AND mime=? AND size=? AND path!=?',
                           matching).fetchall()
    db.execute('UPDATE assets SET path=? WHERE sha256=? AND mime=? AND size=? AND path!=?', (str(path), *matching))
    for row in old_paths:
        queue_file_cleanup(db, row['path'])
    return str(path)


def register_upload(db, settings, course_id, note_id, job_id, item):
    """The caller holds a write transaction and owns the newly uploaded temporary file."""
    path = item.path
    if not item.mime.startswith('video/'):
        path = materialize_blob(db, settings, item)
    db.execute('INSERT INTO assets(id,course_id,note_id,job_id,name,mime,path,size,sha256) VALUES (?,?,?,?,?,?,?,?,?)',
               (item.id, course_id, note_id, job_id, item.name, item.mime, path, item.size, item.sha256))
    if path != item.path:
        queue_file_cleanup(db, item.path)
    return path


def storage_usage(db, owner=None):
    condition = 'WHERE c.owner_id=?' if owner is not None else ''
    identity = "mime,CASE WHEN mime LIKE 'video/%' THEN path ELSE coalesce(sha256,path) END" if owner is not None else 'path'
    args = (owner, MAX_TRANSCRIPT_BYTES) if owner is not None else (MAX_TRANSCRIPT_BYTES,)
    return db.execute(f'''WITH usage AS (
        SELECT a.size,a.path,a.mime,a.sha256,({TEMPORARY_VIDEO}) AS temporary,
            json_extract(j.payload,'$.transcriptAssetId') IS NULL AS pending
        FROM assets a JOIN courses c ON c.id=a.course_id
        LEFT JOIN jobs j ON j.id=a.job_id {condition}
        ), stored_blobs AS (SELECT max(size) AS size FROM usage WHERE NOT temporary GROUP BY {identity})
        SELECT (SELECT coalesce(sum(size),0) FROM stored_blobs) AS stored,
            coalesce(sum(CASE WHEN temporary THEN size ELSE 0 END),0) AS temporary,
            coalesce(sum(CASE WHEN temporary AND pending THEN ? ELSE 0 END),0) AS reserved
        FROM usage''', args).fetchone()


def upload_charge(db, settings, item, owner=None):
    if owner is None:
        path = blob_path(settings, item)
        root = settings.jobs if item.mime == 'application/pdf' else settings.data / 'assets'
        candidates = db.execute('SELECT DISTINCT path FROM assets WHERE sha256=? AND mime=? AND size=?',
                                (item.sha256, item.mime, item.size))
        if regular_upload(path, root) and path.stat().st_size == item.size:
            return 0
        for row in candidates:
            candidate = Path(row['path'])
            if regular_upload(candidate, root) and candidate.stat().st_size == item.size:
                return 0
        return item.size
    exists = db.execute('''SELECT 1 FROM assets a JOIN courses c ON c.id=a.course_id
        WHERE c.owner_id=? AND a.sha256=? AND a.mime=? AND a.size=?''',
                        (owner, item.sha256, item.mime, item.size)).fetchone()
    return 0 if exists else item.size


def queue_file_cleanup(db, path):
    db.execute('INSERT OR IGNORE INTO media_garbage(path) VALUES (?)', (str(path),))
    db.pending_file_cleanup = True


def collect_file_cleanup(db, settings):
    """Hold the write lock while checking references and unlinking committed garbage."""
    db.execute('DELETE FROM file_leases WHERE expires<=?', (now(),))
    rows = db.execute('''SELECT g.path FROM media_garbage g WHERE NOT EXISTS (
        SELECT 1 FROM file_leases WHERE path=g.path)''').fetchall()
    for row in rows:
        path = Path(row['path'])
        if not db.execute('SELECT 1 FROM assets WHERE path=?', (str(path),)).fetchone():
            try:
                if any(regular_upload(path, root) for root in (settings.jobs, settings.data / 'assets')):
                    path.unlink(missing_ok=True)
            except OSError:
                log.warning('Deferred cleanup could not remove an asset file')
                continue
        db.execute('DELETE FROM media_garbage WHERE path=?', (str(path),))


def collect_storage_garbage(settings):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        collect_file_cleanup(db, settings)


def remove_asset(db, settings, asset):
    """The caller holds a write transaction. Keep any shared physical file."""
    db.execute('DELETE FROM assets WHERE id=?', (asset['id'],))
    queue_file_cleanup(db, asset['path'])


def discard_job_video(db, settings, job):
    asset = db.execute("SELECT * FROM assets WHERE id=? AND job_id=? AND mime LIKE 'video/%'",
                       (job.get('sourceAssetId'), job['id'])).fetchone()
    digest = job.get('sourceSha256') or (asset['sha256'] if asset else None)
    if asset:
        path = Path(asset['path'])
        if not digest and regular_upload(path, settings.jobs):
            with path.open('rb') as file:
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
        remove_asset(db, settings, asset)
    elif job.get('mode') == 'real' and job.get('videoPath'):
        directory = settings.jobs / job['id']
        path = directory / job['videoPath']
        if path.parent == directory and regular_upload(path, settings.jobs):
            if not digest:
                with path.open('rb') as file:
                    digest = hashlib.file_digest(file, 'sha256').hexdigest()
            queue_file_cleanup(db, path)
    db.execute("""UPDATE jobs SET payload=json_set(json_remove(payload,'$.sourceAssetId',
        '$.videoPath','$.temporaryVideo'),'$.sourceSha256',?) WHERE id=?""", (digest, job['id']))


def transcript_file(settings, asset):
    path = Path(asset['path'])
    if not regular_upload(path, settings.data / 'assets'):
        return None
    try:
        with path.open('rb') as file:
            raw = file.read(MAX_TRANSCRIPT_BYTES + 1)
        if len(raw) > MAX_TRANSCRIPT_BYTES:
            return None
        text = clean_transcript(raw.decode('utf-8'))
    except (FileNotFoundError, UnicodeError):
        return None
    if len(text.strip()) < 10 or len(text) > MAX_TRANSCRIPT_CHARS:
        return None
    return raw, text


def set_job_transcript(db, job_id, asset_id, source_digest, reused):
    db.execute("""UPDATE jobs SET payload=json_set(payload,'$.transcriptAssetId',?,
        '$.sourceSha256',coalesce(?,json_extract(payload,'$.sourceSha256')),
        '$.transcriptReused',json(?)) WHERE id=?""", (asset_id, source_digest, json.dumps(reused), job_id))


def transcript_actor(db, job, *, existing=False):
    from .worker import JobError, JobStopped, check_active
    row = db.execute("""SELECT j.created_by,j.status,u.disabled,
        json_extract(j.payload,'$.videoArchive') AS archive
        FROM jobs j JOIN courses c ON c.id=j.course_id JOIN users u ON u.id=c.owner_id
        WHERE j.id=? AND j.course_id=?""", (job['id'], job['courseId'])).fetchone()
    if not row:
        raise JobStopped()
    if row['archive'] == 'processing':
        # The course owner authorizes conversion of a retained historical source.
        if row['disabled']:
            raise JobError('ACCESS_REVOKED', 'Права на запись в категорию отозваны.')
    elif not (existing and row['status'] == job.get('status') and row['status'] in ('ready', 'failed', 'cancelled')):
        check_active(db, job['id'])
    return row['created_by']


def existing_job_transcript(settings, job, *, mark_reused):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        asset = db.execute("""SELECT a.* FROM jobs j JOIN assets a
            ON a.id=json_extract(j.payload,'$.transcriptAssetId') AND a.course_id=j.course_id
            WHERE j.id=? AND j.course_id=? AND a.mime='text/plain'""", (job['id'], job['courseId'])).fetchone()
        existing = transcript_file(settings, asset) if asset else None
        if existing is None:
            return None
        transcript_actor(db, job, existing=True)
        raw, text = existing
        digest = hashlib.sha256(raw).hexdigest()
        if asset['sha256'] != digest:
            db.execute('UPDATE assets SET sha256=?,size=? WHERE id=?', (digest, len(raw), asset['id']))
        item = Upload(asset['id'], asset['name'], asset['mime'], asset['path'], len(raw), digest)
        materialize_blob(db, settings, item)
        original_source = job.get('sourceSha256') or asset['legacy_source_sha256']
        if mark_reused:
            set_job_transcript(db, job['id'], asset['id'], original_source, True)
        return text, original_source


def insert_job_transcript(db, settings, job, source_digest, language, item, reused, key):
    created_by = transcript_actor(db, job)
    path = materialize_blob(db, settings, item)
    # The source may belong to another course. Only its immutable bytes are shared.
    name = Path(job.get('sourceName') or 'Расшифровка').stem + '.txt'
    db.execute("""INSERT INTO assets(id,course_id,name,mime,path,size,sha256,transcript_language,created_by)
        VALUES (?,?,?,'text/plain',?,?,?,?,?)""",
               (item.id, job['courseId'], name, path, item.size, item.sha256, language, created_by))
    if key:
        db.execute('INSERT INTO transcript_recognition VALUES (?,?,?,?)', (item.id, *key))
    set_job_transcript(db, job['id'], item.id, source_digest, reused)


def reuse_recognition(settings, job, source_digest, key):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        transcript_actor(db, job)
        assets = db.execute("""SELECT DISTINCT a.path,a.size,a.sha256 FROM speech_results s
            JOIN assets a ON a.sha256=s.text_sha256 AND a.mime='text/plain'
            WHERE s.audio_sha256=? AND s.language=? AND s.engine=?""", key).fetchall()
        for asset in assets:
            existing = transcript_file(settings, asset)
            if existing is None:
                continue
            raw, text = existing
            if hashlib.sha256(raw).hexdigest() != asset['sha256']:
                continue
            # The public reuse flag must never expose another category's activity.
            visible_reuse = db.execute("""SELECT 1 FROM transcript_recognition r JOIN assets a ON a.id=r.asset_id
                WHERE r.audio_sha256=? AND r.language=? AND r.engine=? AND a.course_id=? AND a.sha256=?""",
                                       (*key, job['courseId'], asset['sha256'])).fetchone()
            item = Upload(uid(), '', 'text/plain', asset['path'], len(raw), asset['sha256'])
            insert_job_transcript(db, settings, job, source_digest, key[1], item, bool(visible_reuse), key)
            return text
    return None


def store_recognition(settings, job, source_digest, language, text, key):
    from .worker import JobError
    encoded = text.encode('utf-8')
    if len(text.strip()) < 10:
        raise JobError('NO_SPEECH', 'Whisper не обнаружил достаточно речи. Проверьте звуковую дорожку.')
    if len(text) > MAX_TRANSCRIPT_CHARS or len(encoded) > MAX_TRANSCRIPT_BYTES:
        raise JobError('TRANSCRIPT_LIMIT', 'Расшифровка слишком большая.')
    digest = hashlib.sha256(encoded).hexdigest()
    directory = settings.data / 'assets'
    with tempfile.NamedTemporaryFile(dir=directory) as stream:
        stream.write(encoded)
        stream.flush()
        item = Upload(uid(), '', 'text/plain', stream.name, len(encoded), digest)
        index_matching_legacy_uploads(settings, job['courseId'], item, directory)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            insert_job_transcript(db, settings, job, source_digest, language, item, False, key)
            if key:
                db.execute('INSERT OR REPLACE INTO speech_results VALUES (?,?,?,?)', (*key, digest))


def job_video(settings, job):
    from .worker import JobError
    with connect(settings) as db:
        asset = db.execute("""SELECT path,sha256 FROM assets WHERE id=? AND job_id=? AND course_id=?
            AND mime LIKE 'video/%'""", (job.get('sourceAssetId'), job['id'], job['courseId'])).fetchone()
    if not asset and (job.get('sourceAssetId') or not job.get('videoPath')):
        raise JobError('FILE_MISSING', 'Исходное видео недоступно. Добавьте его в новое задание.')
    path = Path(asset['path']) if asset else settings.jobs / job['id'] / job['videoPath']
    if not regular_upload(path, settings.jobs) or path.parent.parent != settings.jobs:
        raise JobError('FILE_MISSING', 'Исходное видео недоступно. Добавьте его в новое задание.')
    digest = asset['sha256'] if asset else job.get('sourceSha256')
    if not digest:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if asset:
            with connect(settings) as db:
                db.execute('UPDATE assets SET sha256=? WHERE id=? AND sha256 IS NULL',
                           (digest, job['sourceAssetId']))
    return path, digest


def recognition_key(result, language):
    digest, engine = result.get('audioSha256'), result.get('engineVersion')
    if all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) for value in (digest, engine)):
        return digest, language, engine
    return None


def discard_prepared_audio(settings, job_id):
    if re.fullmatch(r'[0-9a-f-]{36}', job_id):
        path = settings.jobs / job_id / 'recognition.wav'
        if regular_upload(path, settings.jobs):
            path.unlink(missing_ok=True)


def prepare_transcript(settings, rpc, job, *, mark_reused=True):
    from .worker import JobError
    existing = existing_job_transcript(settings, job, mark_reused=mark_reused)
    if existing:
        text, source_digest = existing
    else:
        language = job.get('language')
        if language not in ('ru', 'en', 'auto'):
            language = 'ru'
        with connect(settings) as db:
            transcript_actor(db, job)
        video, source_digest = job_video(settings, job)
        body = {'jobId': job['id'], 'video': video.name, 'sourceJobId': video.parent.name, 'language': language}
        try:
            result = rpc(settings, settings.media_url, '/transcribe', {**body, 'prepareAudio': True},
                         timeout=AUDIO_NORMALIZATION_TIMEOUT + 120)
            key = recognition_key(result, language)
            text = reuse_recognition(settings, job, source_digest, key) if key else None
            if text is None:
                if key:
                    result = rpc(settings, settings.media_url, '/transcribe',
                                 {**body, 'preparedAudio': key[0]}, timeout=SPEECH_RECOGNITION_TIMEOUT + 60)
                    actual_key = recognition_key(result, language)
                    if not actual_key or actual_key[0] != key[0]:
                        raise JobError('AUDIO_CHANGED', 'Подготовленная звуковая дорожка изменилась.')
                    key = actual_key
                text = clean_transcript(result.get('text') or '')
                store_recognition(settings, job, source_digest, language, text, key)
        finally:
            discard_prepared_audio(settings, job['id'])
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        discard_job_video(db, settings, {**job, 'sourceSha256': source_digest})
    return text


def media_result(settings, rpc, asset_id, operation, body, timeout, *, asset=None):
    """The API or worker authorizes the owning course before each call."""
    if not asset_id:  # Historical job records with only a job-local filename.
        return rpc(settings, settings.media_url, operation, body, timeout=timeout), False
    if asset is not None:
        return process_media_asset(settings, rpc, asset_id, asset, operation, body, timeout)
    from .worker import JobError
    lease = uid()
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        asset = db.execute('SELECT course_id,job_id,path,sha256 FROM assets WHERE id=?', (asset_id,)).fetchone()
        if not asset:
            raise JobError('FILE_MISSING', 'Файл задания недоступен.')
        db.execute('INSERT INTO file_leases VALUES (?,?,?)', (lease, asset['path'], now() + (timeout + 60) * 1000))
    try:
        return process_media_asset(settings, rpc, asset_id, asset, operation, body, timeout)
    finally:
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM file_leases WHERE id=?', (lease,))
            if not db.execute('SELECT 1 FROM assets WHERE path=?', (asset['path'],)).fetchone():
                queue_file_cleanup(db, asset['path'])


def process_media_asset(settings, rpc, asset_id, asset, operation, body, timeout):
    path = Path(asset['path'])
    if not regular_upload(path, settings.jobs):
        from .worker import JobError
        raise JobError('FILE_MISSING', 'Файл задания недоступен.')
    body = {**body, 'pdf': path.name, 'sourceJobId': path.parent.name}
    if operation != '/slides':
        return rpc(settings, settings.media_url, operation, body, timeout=timeout), False
    digest = asset['sha256']
    if not digest:
        # Pre-migration uploads acquire their identity on the next processing request.
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with connect(settings) as db:
            db.execute('UPDATE assets SET sha256=? WHERE id=? AND sha256 IS NULL', (digest, asset_id))
    # The worker is a singleton. Identical digests share one extraction in flight.
    with _result_locks[hash(digest) % len(_result_locks)]:
        return cached_pdf_text(settings, rpc, asset_id, body, timeout, asset['course_id'], digest)


def cached_pdf_text(settings, rpc, asset_id, body, timeout, course_id, digest):
    with connect(settings) as db:
        cached = db.execute('SELECT version,result FROM pdf_text_results WHERE digest=?', (digest,)).fetchone()
    result = rpc(settings, settings.media_url, '/slides',
                 {**body, 'cachedVersion': cached['version'] if cached else ''}, timeout=timeout)
    if cached and result.get('unchanged') is True:
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE pdf_text_results SET touched=? WHERE digest=?', (now(), digest))
            already_visible = db.execute("""SELECT 1 FROM media_results WHERE course_id=? AND digest=?
                AND operation='/slides' AND variant='' AND version=?""",
                                         (course_id, digest, cached['version'])).fetchone()
            mark_pdf_result(db, asset_id, course_id, digest, cached['version'])
        return json.loads(cached['result']), bool(already_visible)
    version = result.pop('cacheVersion', None)
    if version:
        payload = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
        if len(payload) <= MAX_CACHE_BYTES:
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                # A concurrent deletion must not recreate an unreferenced result.
                db.execute('''INSERT OR REPLACE INTO pdf_text_results
                    SELECT ?,?,?,? WHERE EXISTS (SELECT 1 FROM assets WHERE id=?)''',
                           (digest, version, payload, now(), asset_id))
                mark_pdf_result(db, asset_id, course_id, digest, version)
                db.execute('''DELETE FROM pdf_text_results WHERE digest IN (
                    SELECT digest FROM (SELECT digest,
                      sum(length(result)) OVER (ORDER BY touched DESC,digest) AS bytes,
                      row_number() OVER (ORDER BY touched DESC,digest) AS entries FROM pdf_text_results)
                    WHERE bytes>? OR entries>?)''', (MAX_CACHE_BYTES, MAX_CACHE_ENTRIES))
    return result, False


def mark_pdf_result(db, asset_id, course_id, digest, version):
    db.execute("""INSERT OR REPLACE INTO media_results
        SELECT ?,?,'/slides','',?,x'',? WHERE EXISTS (SELECT 1 FROM assets WHERE id=?)""",
               (course_id, digest, version, now(), asset_id))
    db.execute('''DELETE FROM media_results WHERE rowid IN
        (SELECT rowid FROM media_results ORDER BY touched DESC,rowid DESC LIMIT -1 OFFSET ?)''',
               (MAX_CACHE_ENTRIES * 4,))
