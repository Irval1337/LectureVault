import fcntl
import json
import logging
import os
import time
from pathlib import Path

import httpx

from .config import Settings
from .db import connect, job_json, now, uid

log = logging.getLogger('lecturevault.worker')
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
        raise JobError('ACCESS_REVOKED', 'Права на запись в курс отозваны.')


def set_stage(settings, job_id, status):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        check_active(db, job_id)
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job_id))
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
            set_stage(settings, job_id, 'transcribing')
            result = rpc(settings, settings.media_url, '/transcribe', {'jobId': job_id, 'video': job['videoPath'], 'language': job.get('language', 'ru')})
            transcript = result['text']
            if len(transcript.strip()) < 10:
                raise JobError('NO_SPEECH', 'Whisper не обнаружил достаточно речи. Проверьте звуковую дорожку.')
            set_stage(settings, job_id, 'preparing')
            slides = ''
            if job.get('pdfPath'):
                result = rpc(settings, settings.media_url, '/slides', {'jobId': job_id, 'pdf': job['pdfPath']}, timeout=180)
                slides = result['text']
            set_stage(settings, job_id, 'generating')
            result = rpc(settings, settings.provider_url(row['created_by']), '/generate', {'userId': row['created_by'], 'provider': job.get('provider', 'codex'), 'model': job.get('model', 'gpt-6.1-sol'), 'effort': job.get('effort', 'medium'), 'title': job['title'], 'preset': job['preset'], 'length': job['length'], 'instructions': job['instructions'], 'transcript': transcript[:140000], 'slides': slides[:40000]}, timeout=300)
            markdown = result['markdown']
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
                    raise JobError('COURSE_MISSING', 'Курс удалён.')
                usage = db.execute('SELECT count(*),coalesce(sum(length(CAST(n.markdown AS BLOB))),0) FROM notes n JOIN courses c ON c.id=n.course_id WHERE c.owner_id=?', (owner['owner_id'],)).fetchone()
                if usage[0] >= 5000 or usage[1] + len(markdown.encode()) > 25 * 1024 * 1024:
                    raise JobError('NOTE_QUOTA', 'Лимит заметок владельца курса исчерпан. Удалите ненужные заметки и повторите.')
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?)', (note_id, job['courseId'], job['title'], markdown, 'generated', 1, now(), now(), job_id, int(job['mode'] == 'mock')))
            db.execute("UPDATE jobs SET status='ready',completed_at=?,note_id=?,error=NULL,error_code=NULL WHERE id=?", (now(), note_id, job_id))
        log.info('job=%s stage=ready', job_id)
    except JobStopped:
        return
    except Exception as error:
        code = error.code if isinstance(error, JobError) else 'PROCESSING_ERROR'
        message = error.message if isinstance(error, JobError) else 'Обработка завершилась ошибкой. Исходник сохранён; задание можно повторить.'
        with connect(settings) as db:
            db.execute("UPDATE jobs SET status='failed',completed_at=?,error=?,error_code=? WHERE id=?", (now(), message, code, job_id))
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
        db.execute("UPDATE jobs SET status='failed',completed_at=?,error_code='INTERRUPTED',error='Обработка прервана перезапуском. Повторите задание.' WHERE status IN ('acquiring','transcribing','preparing','generating')", (now(),))


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
        while True:
            row = claim(settings)
            if row:
                run_job(settings, row)
            else:
                time.sleep(.4)


if __name__ == '__main__':
    main()
