from concurrent.futures import ThreadPoolExecutor
import threading
import fcntl
import json
import logging
import os
import time
from pathlib import Path

import httpx

from .slides import embed_slides
from .config import Settings
from .media import collect_storage_garbage, discard_job_video, discard_prepared_audio, media_result, prepare_transcript
from .db import connect, job_json, note_asset_reference, now, uid
from .providers import DETAILED_GENERATION_SECONDS, MAX_NOTE_CHARS, MAX_SLIDE_IMAGES, MAX_SLIDE_IMAGE_BYTES, SLIDE_ANALYSIS_TIMEOUT

log = logging.getLogger('lecturevault.worker')
speech_slot = threading.Lock()
STAGES = ['acquiring', 'transcribing', 'preparing', 'generating']
WAITING_GENERATION = 'waiting_generation'
PDF_TIMEOUT_CODES = {'TIMEOUT', 'RUNNER_UNAVAILABLE', 'API_TIMEOUT', 'OPENAI_TIMEOUT', 'API_LIMIT'}


class JobError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class JobStopped(Exception):
    pass


def check_active(db, job_id):
    row = db.execute("""SELECT j.status,j.error_code,
        (owner.disabled=0 AND sender.disabled=0 AND
         (j.created_by=c.owner_id OR (c.workspace='shared' AND m.role='editor'))) AS writable
        FROM jobs j JOIN courses c ON c.id=j.course_id
        LEFT JOIN users owner ON owner.id=c.owner_id LEFT JOIN users sender ON sender.id=j.created_by
        LEFT JOIN members m ON m.course_id=c.id AND m.user_id=j.created_by
        WHERE j.id=?""", (job_id,)).fetchone()
    if not row or row['status'] in ('ready', 'failed', 'cancelled'):
        raise JobStopped()
    if row['error_code'] == 'ACCESS_REVOKED' or not row['writable']:
        raise JobError('ACCESS_REVOKED', 'Права на запись в категорию отозваны.')


def set_stage(settings, job_id, status):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        check_active(db, job_id)
        db.execute("UPDATE jobs SET status=?,payload=json_set(payload,'$.stageStartedAt',?) WHERE id=?", (status, now(), job_id))
    log.info('job=%s stage=%s', job_id, status)


def rpc(settings, service, endpoint, body, timeout=3600):
    try:
        scope = 'media' if service == settings.media_url else 'providers'
        if service not in [settings.media_url, settings.providers_url]:
            raise JobError('INVALID_RUNNER', 'Недопустимый адрес обработчика.')
        response = httpx.post(service + endpoint, json=body, timeout=timeout, trust_env=False, headers={'X-Runner-Key': (settings.secrets / scope / 'runner-key').read_text()})
        if response.status_code != 200:
            detail = response.json().get('detail', {})
            if isinstance(detail, dict):
                raise JobError(detail.get('code', 'RUNNER_ERROR'), detail.get('message', 'Обработчик не выполнил задание.'))
            raise JobError('RUNNER_ERROR', 'Обработчик не выполнил задание.')
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise JobError('RUNNER_UNAVAILABLE', 'Изолированный обработчик недоступен или истекло время ожидания. Можно повторить задание.') from None


def release_temporary_video(settings, job_id):
    try:
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT * FROM jobs WHERE id=? AND status IN ('ready','failed','cancelled')
                AND json_extract(payload,'$.temporaryVideo')=1""", (job_id,)).fetchone()
            if row:
                discard_job_video(db, settings, job_json(row))
    except OSError:
        log.exception('job=%s video cleanup failed', job_id)


def visual_materials(settings, job, page_count, provider_url, selection):
    images = []
    descriptions = []
    batch_size = 0
    deadline = time.monotonic() + 20 * 60

    def describe():
        with connect(settings) as db:
            check_active(db, job['id'])
        reply = rpc(settings, provider_url, '/describe-slides', {**selection, 'slideImages': images},
                    timeout=SLIDE_ANALYSIS_TIMEOUT + 60)
        descriptions.append(reply['slides'])

    try:
        for page in range(1, page_count + 1):
            if time.monotonic() >= deadline:
                return [], '\n\n'.join(descriptions), True
            result, _ = media_result(settings, rpc, job['pdfAssetId'], '/slide-preview',
                                     {'jobId': job['id'], 'page': page}, 105)
            jpeg = result['jpeg']
            if len(images) == MAX_SLIDE_IMAGES or batch_size + len(jpeg) > MAX_SLIDE_IMAGE_BYTES:
                describe()
                images, batch_size = [], 0
            images.append({'page': page, 'jpeg': jpeg})
            batch_size += len(jpeg)
        if not descriptions:
            return images, '', False
        if images:
            describe()
    except JobError as error:
        if error.code not in PDF_TIMEOUT_CODES:
            raise
        log.warning('job=%s slide images incomplete: %s', job['id'], error.code)
        return [], '\n\n'.join(descriptions), True
    return [], '\n\n'.join(descriptions), False


def pdf_materials(settings, job, provider_url, selection):
    asset = job.get('pdfAssetId')
    partial = False
    try:
        result, reused = media_result(settings, rpc, asset, '/slides',
                                      {'jobId': job['id'], 'pdf': job.get('pdfPath', '')}, 300)
    except JobError as error:
        if error.code not in PDF_TIMEOUT_CODES:
            raise
        log.warning('job=%s PDF text incomplete: %s', job['id'], error.code)
        partial, result, reused = True, {}, False
        if asset:
            try:
                result, _ = media_result(settings, rpc, asset, '/slide-preview', {'jobId': job['id'], 'page': 0}, 105)
            except JobError as preview_error:
                if preview_error.code not in PDF_TIMEOUT_CODES:
                    raise
    slides = result.get('text', '')
    pages = result.get('pages', 0)
    images = []
    if job.get('supportsImages') and asset and pages:
        images, visual_text, visual_partial = visual_materials(settings, job, pages, provider_url, selection)
        partial = partial or visual_partial
        slides = '\n\n'.join(filter(None, (slides, visual_text)))
    with connect(settings) as db:
        db.execute("""UPDATE jobs SET payload=json_set(payload,'$.slidesReused',json(?),
            '$.slideTextCondensed',?,'$.slidesPartial',json(?)) WHERE id=?""",
                   (json.dumps(reused), int(bool(result.get('condensed'))), json.dumps(partial), job['id']))
    return slides, pages, images


def run_job(settings, row):
    job = job_json(row)
    job_id = job['id']
    resolved_model = None
    try:
        if job['mode'] == 'mock':
            if settings.environment != 'test':
                raise JobError('MOCK_DISABLED', 'Демонстрационная обработка отключена.')
            for stage in STAGES:
                set_stage(settings, job_id, stage)
                time.sleep(settings.mock_seconds / len(STAGES))
            if job.get('sourceName', '').startswith('https://error.example') and job['attempt'] == 0:
                raise JobError('MOCK_SOURCE_ERROR', 'Демонстрационная ошибка источника. Повторная попытка продолжит mock-сценарий.')
            markdown = (Path(__file__).resolve().parents[1] / 'fixtures/demo-note.md').read_text()
            markdown += '\n\n## Сравнение\n\n| Способ | Свойство |\n| --- | --- |\n| Репликация | Избыточность |\n\n![Демонстрация](/demo-replication.svg)\n\n*Mock: источник не анализировался.*\n'
        else:
            if row['status'] == WAITING_GENERATION:
                transcript = prepare_transcript(settings, rpc, job, mark_reused=False)
            else:
                transcript = transcribe_job(settings, job)
            slides = ''
            slide_count = 0
            slide_images = []
            selection = {'userId': row['created_by'], 'provider': job.get('provider', 'codex'),
                         'model': job.get('model') or '', 'effort': job.get('effort', 'medium'), 'apiBase': job.get('apiBase')}
            provider_url = settings.provider_url(row['created_by'])
            if job.get('pdfPath') or job.get('pdfAssetId'):
                set_stage(settings, job_id, 'preparing')
                slides, slide_count, slide_images = pdf_materials(settings, job, provider_url, selection)
            set_stage(settings, job_id, 'generating')
            timeout = DETAILED_GENERATION_SECONDS if job['length'] == 'detailed' and not job.get('targetWords') else 300
            material = {'transcript': transcript, 'slides': slides, 'pageCount': slide_count, 'slideImages': slide_images}
            options = {key: job.get(key) for key in ('title', 'preset', 'targetWords', 'length', 'instructions')}
            options['slideLimit'] = job.get('slideLimit', 6)
            result = rpc(settings, provider_url, '/generate', {**selection, **options, **material}, timeout=timeout + 30)
            markdown = result['markdown']
            resolved_model = result.get('model')
            if isinstance(markdown, str) and markdown.strip() and job.get('pdfAssetId') and 0 < slide_count <= 500:
                markdown = embed_slides(markdown, job['pdfAssetId'], slide_count, job.get('slideLimit', 6))

        if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > MAX_NOTE_CHARS:
            raise JobError('INVALID_MARKDOWN', 'Обработчик вернул пустой или слишком большой документ.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            check_active(db, job_id)
            note_id = uid()
            existing = db.execute('SELECT id FROM notes WHERE job_id=?', (job_id,)).fetchone()
            if existing:
                note_id = existing['id']
            else:
                owner = db.execute('SELECT owner_id FROM courses WHERE id=?', (job['courseId'],)).fetchone()
                if not owner:
                    raise JobError('COURSE_MISSING', 'Категория удалена.')
                usage = db.execute('SELECT count(*),coalesce(sum(length(CAST(n.markdown AS BLOB))),0) FROM notes n JOIN courses c ON c.id=n.course_id WHERE c.owner_id=?', (owner['owner_id'],)).fetchone()
                if usage[0] >= 5000 or usage[1] + len(markdown.encode()) > 25 * 1024 * 1024:
                    raise JobError('NOTE_QUOTA', 'Лимит заметок владельца категории исчерпан. Удалите ненужные заметки и повторите.')
                db.execute('''INSERT INTO notes(id,course_id,title,markdown,origin,revision,created_at,updated_at,job_id,is_mock,created_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (note_id, job['courseId'], job['title'], markdown, 'generated', 1,
                    now(), now(), job_id, int(job['mode'] == 'mock'), row['created_by']))
                # A user can rename the destination while transcription is running.
                category = db.execute("SELECT json_extract(payload,'$.category') FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                if category:
                    db.execute('INSERT INTO note_categories VALUES (?,?)', (note_id, category))
            if resolved_model and resolved_model != job.get('model'):
                db.execute("UPDATE jobs SET payload=json_set(payload,'$.model',?) WHERE id=?", (resolved_model, job_id))
            db.execute("UPDATE jobs SET status='ready',completed_at=?,note_id=?,error=NULL,error_code=NULL WHERE id=?", (now(), note_id, job_id))
        log.info('job=%s stage=ready', job_id)
    except JobStopped:
        return
    except Exception as error:
        fail_job(settings, job_id, error)
    finally:
        release_temporary_video(settings, job_id)


def fail_job(settings, job_id, error):
    code = error.code if isinstance(error, JobError) else 'PROCESSING_ERROR'
    message = error.message if isinstance(error, JobError) else 'Обработка завершилась ошибкой.'
    with connect(settings) as db:
        db.execute("""UPDATE jobs SET payload=json_set(payload,'$.failedStage',status),status='failed',
                      completed_at=?,error=?,error_code=? WHERE id=? AND status NOT IN ('ready','failed','cancelled')""",
                   (now(), message, code, job_id))
    log.warning('job=%s error=%s', job_id, code)


def transcribe_job(settings, job):
    if job.get('transcriptAssetId'):
        return prepare_transcript(settings, rpc, job)
    with speech_slot:
        (settings.jobs / job['id'] / 'recognition.progress').unlink(missing_ok=True)
        set_stage(settings, job['id'], 'transcribing')
        return prepare_transcript(settings, rpc, job)


def run_recognition(settings, row):
    job = job_json(row)
    try:
        transcribe_job(settings, job)
        set_stage(settings, job['id'], WAITING_GENERATION)
    except JobStopped:
        return
    except Exception as error:
        fail_job(settings, job['id'], error)
    finally:
        release_temporary_video(settings, job['id'])


def speech_busy(db):
    return db.execute("""SELECT 1 FROM jobs WHERE json_extract(payload,'$.videoArchive')='processing'
        OR (status IN ('acquiring','transcribing') AND json_extract(payload,'$.mode')='real'
            AND json_extract(payload,'$.transcriptAssetId') IS NULL) LIMIT 1""").fetchone() is not None


def claim(settings, lane=None):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        if lane == 'speech' and speech_busy(db):
            return None
        needs_speech = "coalesce(json_extract(j.payload,'$.mode'),'real')='real' AND json_extract(j.payload,'$.transcriptAssetId') IS NULL"
        if lane == 'speech':
            eligibility = f"j.status='queued' AND ({needs_speech})"
        elif lane == 'generation':
            eligibility = f"(j.status=? OR (j.status='queued' AND NOT ({needs_speech})))"
        else:
            eligibility = f"j.status='queued' AND (NOT ? OR NOT ({needs_speech}))"
        args = () if lane == 'speech' else ((WAITING_GENERATION,) if lane == 'generation' else (speech_busy(db),))
        # CLI requests share an account profile lock. Only the provider lane waits for that profile.
        if lane != 'speech':
            generating = "'preparing','generating'" if lane == 'generation' else "'acquiring','transcribing','preparing','generating'"
            eligibility += f""" AND (json_extract(j.payload,'$.mode')='mock' OR NOT EXISTS (
                SELECT 1 FROM jobs running WHERE running.created_by=j.created_by
                AND running.status IN ({generating})
                AND json_extract(running.payload,'$.mode')='real'
                AND (coalesce(json_extract(j.payload,'$.provider'),'codex')<>'openai'
                    OR coalesce(json_extract(running.payload,'$.provider'),'codex')<>'openai')
            ))"""
        row = db.execute(f"""SELECT j.* FROM jobs j JOIN courses c ON c.id=j.course_id
            JOIN users owner ON owner.id=c.owner_id AND owner.disabled=0
            JOIN users sender ON sender.id=j.created_by AND sender.disabled=0
            WHERE {eligibility}
            ORDER BY coalesce((SELECT max(started_at) FROM jobs WHERE created_by=j.created_by),0),
                j.created_at,j.rowid LIMIT 1""", args).fetchone()
        if row:
            stage = 'preparing' if lane == 'generation' and job_json(row).get('mode') != 'mock' else 'acquiring'
            db.execute('UPDATE jobs SET status=?,started_at=coalesce(started_at,?) WHERE id=?', (stage, now(), row['id']))
        return row


def recover_interrupted(settings):
    with connect(settings) as db:
        db.execute("UPDATE jobs SET payload=json_set(payload,'$.failedStage',status),status='failed',completed_at=?,error_code='INTERRUPTED',error='Обработка прервана перезапуском.' WHERE status IN ('acquiring','transcribing','preparing','generating')", (now(),))
        db.execute("""UPDATE jobs SET payload=json_set(payload,'$.videoArchive','queued')
            WHERE json_extract(payload,'$.videoArchive')='processing'""")
        pending_cleanup = db.execute("""SELECT id FROM jobs WHERE status IN ('ready','failed','cancelled')
            AND json_extract(payload,'$.temporaryVideo')=1""").fetchall()
        prepared_ids = db.execute('SELECT id FROM jobs').fetchall()
    for row in pending_cleanup:
        release_temporary_video(settings, row['id'])
    for row in prepared_ids:
        discard_prepared_audio(settings, row['id'])
    collect_storage_garbage(settings)


def claim_video_archive(settings):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        if speech_busy(db):
            return None
        row = db.execute("""SELECT j.* FROM jobs j JOIN courses c ON c.id=j.course_id
            JOIN users owner ON owner.id=c.owner_id AND owner.disabled=0
            WHERE json_extract(j.payload,'$.videoArchive')='queued' ORDER BY j.rowid LIMIT 1""").fetchone()
        if row:
            db.execute("UPDATE jobs SET payload=json_set(payload,'$.videoArchive','processing') WHERE id=?", (row['id'],))
        return row


def archive_video(settings, row):
    try:
        with speech_slot:
            job = job_json(row)
            with connect(settings) as db:
                if note_asset_reference(db, job['courseId'], job['sourceAssetId']):
                    raise JobError('VIDEO_IN_USE', 'Видео связано с конспектом или его предыдущей версией.')
            prepare_transcript(settings, rpc, job_json(row))
    except Exception as error:
        message = error.message if isinstance(error, JobError) else 'Не удалось сохранить расшифровку.'
        with connect(settings) as db:
            db.execute("""UPDATE jobs SET payload=json_set(payload,'$.videoArchive','failed',
                '$.videoArchiveError',?) WHERE id=?""", (message, row['id']))
        log.warning('job=%s video conversion failed', row['id'])
    else:
        with connect(settings) as db:
            db.execute("UPDATE jobs SET payload=json_remove(payload,'$.videoArchive','$.videoArchiveError') WHERE id=?", (row['id'],))


def main():
    if os.geteuid() == 0:
        raise SystemExit('Worker must run as a non-root user')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    settings = Settings()
    with (settings.data / 'worker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Only one worker may run') from None
        recover_interrupted(settings)
        with ThreadPoolExecutor(max_workers=1) as speech, ThreadPoolExecutor(max_workers=2) as generation:
            recognizing = None
            writing = set()
            prefer_archive = False
            next_cleanup = time.monotonic() + 30
            while True:
                if time.monotonic() >= next_cleanup:
                    collect_storage_garbage(settings)
                    next_cleanup = time.monotonic() + 30
                if recognizing is not None and recognizing.done():
                    recognizing.result()
                    recognizing = None
                for task in list(writing):
                    if task.done():
                        task.result()
                        writing.remove(task)
                if recognizing is None:
                    if prefer_archive and (archive := claim_video_archive(settings)):
                        recognizing = speech.submit(archive_video, settings, archive)
                        prefer_archive = False
                    elif row := claim(settings, 'speech'):
                        recognizing = speech.submit(run_recognition, settings, row)
                        prefer_archive = True
                    elif not prefer_archive and (archive := claim_video_archive(settings)):
                        recognizing = speech.submit(archive_video, settings, archive)
                if len(writing) < 2 and (row := claim(settings, 'generation')):
                    writing.add(generation.submit(run_job, settings, row))
                time.sleep(.4)


if __name__ == '__main__':
    main()
