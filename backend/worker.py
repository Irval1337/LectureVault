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
from .media import media_result
from .db import connect, job_json, now, uid

log = logging.getLogger('lecturevault.worker')
speech_slot = threading.Lock()
STAGES = ['acquiring', 'transcribing', 'preparing', 'generating']


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


def run_job(settings, row):
    job = job_json(row)
    job_id = job['id']
    resolved_model = None
    try:
        set_stage(settings, job_id, 'acquiring')
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
            (settings.jobs / job_id / 'recognition.progress').unlink(missing_ok=True)
            with speech_slot:
                set_stage(settings, job_id, 'transcribing')
                result, reused = media_result(settings, rpc, job.get('sourceAssetId'), '/transcribe',
                                              {'jobId': job_id, 'video': job['videoPath'], 'language': job.get('language', 'ru')}, 3600)
                with connect(settings) as db:
                    db.execute("UPDATE jobs SET payload=json_set(payload,'$.transcriptReused',json(?)) WHERE id=?", (json.dumps(reused), job_id))
            transcript = result['text']
            if len(transcript.strip()) < 10:
                raise JobError('NO_SPEECH', 'Whisper не обнаружил достаточно речи. Проверьте звуковую дорожку.')
            slides = ''
            slide_count = 0
            if job.get('pdfPath'):
                set_stage(settings, job_id, 'preparing')
                result, reused = media_result(settings, rpc, job.get('pdfAssetId'), '/slides',
                                              {'jobId': job_id, 'pdf': job['pdfPath']}, 180)
                with connect(settings) as db:
                    db.execute("UPDATE jobs SET payload=json_set(payload,'$.slidesReused',json(?)) WHERE id=?", (json.dumps(reused), job_id))
                slides = result['text']
                if result.get('condensed'):
                    with connect(settings) as db:
                        db.execute("UPDATE jobs SET payload=json_set(payload,'$.slideTextCondensed',1) WHERE id=?", (job_id,))
                slide_count = result.get('pages', 0)
            set_stage(settings, job_id, 'generating')
            result = rpc(settings, settings.provider_url(row['created_by']), '/generate', {'userId': row['created_by'], 'provider': job.get('provider', 'codex'), 'model': job.get('model') or '', 'effort': job.get('effort', 'medium'), 'apiBase': job.get('apiBase'), 'title': job['title'], 'preset': job['preset'], 'targetWords': job.get('targetWords'), 'slideLimit': job.get('slideLimit', 6), 'length': job['length'], 'instructions': job['instructions'], 'transcript': transcript[:140000], 'slides': slides[:40000]}, timeout=300)
            markdown = result['markdown']
            resolved_model = result.get('model')
            if isinstance(markdown, str) and markdown.strip() and job.get('pdfAssetId') and 0 < slide_count <= 500:
                markdown = embed_slides(markdown, job['pdfAssetId'], slide_count, job.get('slideLimit', 6))

        if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > 200000:
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
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?)', (note_id, job['courseId'], job['title'], markdown, 'generated', 1, now(), now(), job_id, int(job['mode'] == 'mock')))
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
        code = error.code if isinstance(error, JobError) else 'PROCESSING_ERROR'
        message = error.message if isinstance(error, JobError) else 'Обработка завершилась ошибкой. Исходник сохранён; задание можно повторить.'
        with connect(settings) as db:
            db.execute("UPDATE jobs SET payload=json_set(payload,'$.failedStage',status),status='failed',completed_at=?,error=?,error_code=? WHERE id=?", (now(), message, code, job_id))
        log.warning('job=%s error=%s', job_id, code)


def claim(settings):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("""SELECT j.* FROM jobs j JOIN courses c ON c.id=j.course_id
            JOIN users owner ON owner.id=c.owner_id AND owner.disabled=0
            JOIN users sender ON sender.id=j.created_by AND sender.disabled=0
            WHERE j.status='queued' ORDER BY j.created_at,j.rowid LIMIT 1""").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='acquiring',started_at=? WHERE id=? AND status='queued'", (now(), row['id']))
        return row


def recover_interrupted(settings):
    with connect(settings) as db:
        db.execute("UPDATE jobs SET payload=json_set(payload,'$.failedStage',status),status='failed',completed_at=?,error_code='INTERRUPTED',error='Обработка прервана перезапуском. Повторите задание.' WHERE status IN ('acquiring','transcribing','preparing','generating')", (now(),))


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
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = set()
            while True:
                for task in list(running):
                    if task.done():
                        task.result()
                        running.remove(task)
                row = claim(settings) if len(running) < 2 else None
                if row:
                    running.add(pool.submit(run_job, settings, row))
                else:
                    time.sleep(.4)


if __name__ == '__main__':
    main()
