"""Course-local upload identity and bounded, runner-validated media results."""
import hashlib
import json
import threading
from pathlib import Path
from typing import NamedTuple

from .db import connect, now

MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_ENTRIES = 2048
_result_locks = [threading.Lock() for _ in range(64)]


class Upload(NamedTuple):
    id: str
    name: str
    mime: str
    path: str
    size: int
    sha256: str


def regular_upload(path, root):
    return (path.is_relative_to(root) and not any(part.is_symlink() for part in (path, *path.parents))
            and path.is_file())


def index_matching_legacy_uploads(settings, course_id, upload, root):
    # Limit lazy migration to same-size, same-format candidates in this course.
    # File I/O happens outside SQLite write transactions.
    with connect(settings) as db:
        candidates = db.execute("""SELECT DISTINCT path FROM assets WHERE course_id=?
            AND mime=? AND size=? AND sha256 IS NULL""", (course_id, upload.mime, upload.size)).fetchall()
    for row in candidates:
        path = Path(row['path'])
        if not regular_upload(path, root):
            continue
        try:
            with path.open('rb') as file:
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
        except FileNotFoundError:
            continue
        with connect(settings) as db:
            db.execute('UPDATE assets SET sha256=? WHERE course_id=? AND path=? AND sha256 IS NULL',
                       (digest, course_id, str(path)))


def register_upload(db, settings, course_id, note_id, job_id, item):
    """The caller holds a write transaction and owns the newly uploaded temporary file."""
    root = settings.jobs if job_id else settings.data / 'assets'
    path = item.path
    for row in db.execute('SELECT path FROM assets WHERE course_id=? AND sha256=? AND mime=? AND size=?',
                          (course_id, item.sha256, item.mime, item.size)):
        candidate = Path(row['path'])
        if regular_upload(candidate, root) and candidate.stat().st_size == item.size:
            path = row['path']
            break
    db.execute('INSERT INTO assets(id,course_id,note_id,job_id,name,mime,path,size,sha256) VALUES (?,?,?,?,?,?,?,?,?)',
               (item.id, course_id, note_id, job_id, item.name, item.mime, path, item.size, item.sha256))
    if path != item.path:
        Path(item.path).unlink()
    return path


def media_result(settings, rpc, asset_id, operation, body, timeout):
    """The API or worker authorizes the owning course before each call."""
    with connect(settings) as db:
        asset = db.execute('SELECT course_id,job_id,path,sha256 FROM assets WHERE id=?', (asset_id,)).fetchone()
    if not asset_id:  # Historical job records with only a job-local filename.
        return rpc(settings, settings.media_url, operation, body, timeout=timeout), False
    if not asset or not regular_upload(Path(asset['path']), settings.jobs):
        from .worker import JobError
        raise JobError('FILE_MISSING', 'Файл задания недоступен.')
    path = Path(asset['path'])
    digest = asset['sha256']
    if not digest:
        # Pre-migration uploads acquire their identity on the next processing request.
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with connect(settings) as db:
            db.execute('UPDATE assets SET sha256=? WHERE id=? AND sha256 IS NULL', (digest, asset_id))
    source = 'video' if operation == '/transcribe' else 'pdf'
    body = {**body, source: path.name, 'sourceJobId': path.parent.name}
    variant = str(body.get('language', body.get('page', '')))
    key = (asset['course_id'], digest, operation, variant)
    # API requests share one process and the worker owns its two job threads.
    # Serialize identical misses so concurrent requests render/extract only once.
    with _result_locks[hash(key) % len(_result_locks)]:
        return cached_rpc(settings, rpc, asset_id, operation, body, timeout, key)


def cached_rpc(settings, rpc, asset_id, operation, body, timeout, key):
    clause = 'course_id=? AND digest=? AND operation=? AND variant=?'
    with connect(settings) as db:
        cached = db.execute(f'SELECT version,result FROM media_results WHERE {clause}', key).fetchone()
    result = rpc(settings, settings.media_url, operation,
                 {**body, 'cachedVersion': cached['version'] if cached else ''}, timeout=timeout)
    if cached and result.get('unchanged') is True:
        with connect(settings) as db:
            db.execute(f'UPDATE media_results SET touched=? WHERE {clause}', (now(), *key))
        return json.loads(cached['result']), True
    version = result.pop('cacheVersion', None)
    cacheable = operation != '/transcribe' or len(result.get('text', '').strip()) >= 10
    if version and cacheable:
        payload = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
        if len(payload) <= MAX_CACHE_BYTES:
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                # Concurrent course removal must never recreate a deleted cache entry.
                db.execute('''INSERT OR REPLACE INTO media_results
                    SELECT ?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM assets WHERE id=?)''',
                           (*key, version, payload, now(), asset_id))
                db.execute('''DELETE FROM media_results WHERE rowid IN (
                    SELECT id FROM (SELECT rowid AS id,
                      sum(length(result)) OVER (ORDER BY touched DESC,rowid DESC) AS bytes,
                      row_number() OVER (ORDER BY touched DESC,rowid DESC) AS entries FROM media_results)
                    WHERE bytes>? OR entries>?)''', (MAX_CACHE_BYTES, MAX_CACHE_ENTRIES))
    return result, False
