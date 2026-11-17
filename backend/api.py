import hashlib
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from .config import Settings, USERS
from .db import connect, course_json, initialize, job_json, note_json, now, uid
from .providers import CATALOG, valid_options


class CourseInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    workspace: Literal['personal', 'shared'] = 'personal'


class NoteInput(BaseModel):
    courseId: str
    title: str = Field(default='Без названия', min_length=1, max_length=120)
    markdown: str = Field(default='', max_length=200_000)


class NoteEdit(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    markdown: str = Field(max_length=200_000)
    revision: int = Field(ge=1)


class JobInput(BaseModel):
    courseId: str
    title: str = Field(min_length=1, max_length=120)
    sourceType: Literal['file', 'url'] = 'file'
    sourceName: str = Field(default='', max_length=2048)
    preset: Literal['lecture', 'seminar'] = 'lecture'
    length: Literal['short', 'medium', 'detailed'] = 'medium'
    instructions: str = Field(default='', max_length=4000)
    mode: Literal['mock', 'real'] = 'real'
    language: Literal['ru', 'en', 'auto'] = 'ru'
    provider: Literal['codex', 'claude'] = 'codex'
    model: str = Field(default='gpt-6.1-sol', max_length=50)
    effort: Literal['low', 'medium', 'high', 'xhigh'] = 'medium'

    @model_validator(mode='after')
    def options(self):
        if not valid_options(self.provider, self.model, self.effort):
            raise ValueError('Unsupported model or effort')
        return self


def create_app(settings: Settings | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(_app):
        initialize(settings)
        yield

    app = FastAPI(title='LectureVault', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'api', 'testserver'])

    @app.middleware('http')
    async def boundaries(request: Request, call_next):
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            if request.headers.get('x-lecturevault') != '1':
                return JSONResponse({'detail': 'Отсутствует заголовок защиты запроса.'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and (urlparse(origin).hostname not in ['localhost', '127.0.0.1']):
                return JSONResponse({'detail': 'Недопустимый origin.'}, status_code=403)
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'detail': 'Неверный размер запроса.'}, status_code=400)
        limit = 6 * 1024 * 1024 if request.headers.get('content-type', '').startswith('application/json') else 565 * 1024 * 1024
        if length > limit:
            return JSONResponse({'detail': 'Запрос превышает допустимый размер.'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def user(lv_session: str | None = Cookie(default=None)):
        if not lv_session:
            raise HTTPException(401, 'Войдите в демо-аккаунт.')
        with connect(settings) as db:
            row = db.execute('SELECT * FROM sessions WHERE digest=? AND expires>?', (hashlib.sha256(lv_session.encode()).hexdigest(), now())).fetchone()
        if not row:
            raise HTTPException(401, 'Сессия истекла. Войдите снова.')
        return row['user_id']

    def access(db, course_id, user_id, write=False, owner=False):
        course = db.execute('SELECT * FROM courses WHERE id=?', (course_id,)).fetchone()
        role = None
        if course:
            if course['owner_id'] == user_id:
                role = 'owner'
            elif course['workspace'] == 'shared':
                member = db.execute('SELECT role FROM members WHERE course_id=? AND user_id=?', (course_id, user_id)).fetchone()
                if member:
                    role = member['role']
        if not role:
            raise HTTPException(404, 'Курс не найден или недоступен.')
        if (write and role == 'viewer') or (owner and role != 'owner'):
            raise HTTPException(403, 'Недостаточно прав для этого действия.')
        return course, role

    def entity(db, table, entity_id, user_id, write=False):
        # Table names are constants at call sites, never client input.
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (entity_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Материал не найден.')
        access(db, row['course_id'], user_id, write)
        return row

    @app.get('/api/health')
    def health():
        with connect(settings) as db:
            db.execute('SELECT 1')
        return {'ok': True, 'mode': 'local-demo'}

    @app.get('/api/auth')
    def auth_info(lv_session: str | None = Cookie(default=None)):
        current = None
        if lv_session:
            try:
                current = next(u for u in USERS if u['id'] == user(lv_session))
            except HTTPException:
                pass
        return {'users': USERS, 'user': current}

    login_attempts: dict[str, list[float]] = {}

    @app.post('/api/auth/login')
    def login(body: dict, request: Request, response: Response):
        key = request.client.host if request.client else 'local'
        attempts = [t for t in login_attempts.get(key, []) if t > time.monotonic() - 60]
        if len(attempts) >= 30:
            raise HTTPException(429, 'Слишком много попыток входа. Подождите минуту.')
        login_attempts[key] = [*attempts, time.monotonic()]
        if body.get('userId') not in [u['id'] for u in USERS]:
            raise HTTPException(400, 'Выберите демопользователя.')
        token = secrets.token_urlsafe(40)
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (now(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), body['userId'], now() + 8 * 3600 * 1000))
        response.set_cookie('lv_session', token, httponly=True, samesite='strict', max_age=8 * 3600, path='/')
        return {'user': next(u for u in USERS if u['id'] == body['userId'])}

    @app.post('/api/auth/logout')
    def logout(response: Response, lv_session: str | None = Cookie(default=None)):
        if lv_session:
            with connect(settings) as db:
                db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(lv_session.encode()).hexdigest(),))
        response.delete_cookie('lv_session', path='/')
        return {'ok': True}

    @app.get('/api/state')
    def state(user_id=Depends(user)):
        with connect(settings) as db:
            courses = []
            for row in db.execute('SELECT * FROM courses ORDER BY created_at'):
                try:
                    _, role = access(db, row['id'], user_id)
                    courses.append(course_json(row, role))
                except HTTPException:
                    continue
            ids = [c['id'] for c in courses]
            marks = ','.join('?' for _ in ids) or 'NULL'
            notes = [note_json(row) for row in db.execute(f'SELECT * FROM notes WHERE course_id IN ({marks}) ORDER BY created_at', ids)]
            jobs = [job_json(row) for row in db.execute(f'SELECT * FROM jobs WHERE course_id IN ({marks}) ORDER BY created_at', ids)]
        return {'version': 1, 'courses': courses, 'notes': notes, 'jobs': jobs}

    @app.get('/api/providers')
    def providers(user_id=Depends(user)):
        from .worker import JobError, rpc
        try:
            return rpc(settings, settings.provider_url(user_id), '/providers/status', {'userId': user_id}, timeout=35)
        except JobError:
            return {'providers': [{**p, 'connected': False, 'login': {'status': 'unavailable'}} for p in CATALOG]}

    @app.post('/api/providers/{provider}/{action}')
    def provider_action(provider: Literal['codex', 'claude'], action: Literal['connect', 'code', 'disconnect'], body: dict, user_id=Depends(user)):
        from .worker import JobError, rpc
        payload = {'userId': user_id, 'provider': provider}
        if action == 'code':
            code = body.get('code')
            if not isinstance(code, str) or not 4 <= len(code) <= 1000:
                raise HTTPException(422, 'Введите код подтверждения с официальной страницы провайдера.')
            payload['code'] = code
        try:
            return rpc(settings, settings.provider_url(user_id), f'/providers/{action}', payload, timeout=30)
        except JobError as error:
            raise HTTPException(502, error.message) from None

    @app.get('/api/providers/codex/callback')
    def codex_callback(request: Request):
        from .worker import JobError, rpc
        if len(request.url.query) > 12000:
            raise HTTPException(400, 'Invalid callback')
        payload = dict(request.query_params)
        if not payload.get('state') or not (payload.get('code') or payload.get('error')):
            raise HTTPException(400, 'Invalid callback')
        for account in USERS:
            try:
                rpc(settings, settings.provider_url(account['id']), '/providers/callback', payload, timeout=35)
                return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><title>LectureVault</title><p>Вход подтверждён. Вернитесь в LectureVault.</p></html>', headers={'Referrer-Policy': 'no-referrer'})
            except JobError:
                continue
        raise HTTPException(400, 'Вход не подтверждён. Начните подключение заново в LectureVault.')

    @app.post('/api/courses', status_code=201)
    def create_course(body: CourseInput, user_id=Depends(user)):
        title = body.title.strip()
        if not title:
            raise HTTPException(422, 'Введите название курса.')
        with connect(settings) as db:
            if db.execute('SELECT 1 FROM courses WHERE owner_id=? AND workspace=? AND lower(title)=lower(?)', (user_id, body.workspace, title)).fetchone():
                raise HTTPException(409, 'Курс с таким названием уже существует.')
            course_id = uid()
            db.execute('INSERT INTO courses VALUES (?,?,?,?,?)', (course_id, title, body.workspace, user_id, now()))
            if body.workspace == 'shared':
                db.execute('INSERT INTO members VALUES (?,?,?)', (course_id, 'bob' if user_id == 'alice' else 'alice', 'editor'))
        return {'id': course_id}

    @app.get('/api/courses/{course_id}/members')
    def members(course_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            course, _ = access(db, course_id, user_id)
            rows = {r['user_id']: r['role'] for r in db.execute('SELECT * FROM members WHERE course_id=?', (course_id,))}
        return [{**u, 'role': 'owner' if u['id'] == course['owner_id'] else rows.get(u['id'], 'none')} for u in USERS]

    @app.put('/api/courses/{course_id}/members/{member_id}')
    def membership(course_id: str, member_id: str, body: dict, user_id=Depends(user)):
        with connect(settings) as db:
            course, _ = access(db, course_id, user_id, owner=True)
            if course['workspace'] != 'shared' or member_id == course['owner_id'] or member_id not in [u['id'] for u in USERS] or body.get('role') not in ['editor', 'viewer', 'none']:
                raise HTTPException(422, 'Недопустимое изменение роли.')
            db.execute('DELETE FROM members WHERE course_id=? AND user_id=?', (course_id, member_id))
            if body['role'] != 'none':
                db.execute('INSERT INTO members VALUES (?,?,?)', (course_id, member_id, body['role']))
        return {'ok': True}

    @app.post('/api/notes', status_code=201)
    def create_note(body: NoteInput, user_id=Depends(user)):
        if not body.title.strip():
            raise HTTPException(422, 'Введите название заметки.')
        with connect(settings) as db:
            access(db, body.courseId, user_id, write=True)
            note_id = uid()
            db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?)', (note_id, body.courseId, body.title.strip(), body.markdown, 'manual', 1, now(), now(), None, 0))
            result = note_json(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())
        return result

    @app.get('/api/notes/{note_id}')
    def get_note(note_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            return note_json(entity(db, 'notes', note_id, user_id))

    @app.patch('/api/notes/{note_id}')
    def save_note(note_id: str, body: NoteEdit, user_id=Depends(user)):
        if not body.title.strip():
            raise HTTPException(422, 'Введите название заметки.')
        with connect(settings) as db:
            entity(db, 'notes', note_id, user_id, write=True)
            result = db.execute('UPDATE notes SET title=?,markdown=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?', (body.title.strip(), body.markdown, now(), note_id, body.revision))
            if result.rowcount != 1:
                raise HTTPException(409, 'Документ уже изменён. Черновик сохранён; откройте актуальную версию перед повторным сохранением.')
            return note_json(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())

    @app.get('/api/notes/{note_id}/export')
    def export_note(note_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            note = entity(db, 'notes', note_id, user_id)
        return Response(note['markdown'], media_type='text/markdown; charset=utf-8', headers={'Content-Disposition': f"attachment; filename=note.md; filename*=UTF-8''{quote(note['title'], safe='')}.md"})

    async def store_file(file: UploadFile, directory: Path, limit: int, kind: str):
        field = 'pdf' if kind == 'pdf' else 'source'
        label = {'video': 'Видео', 'pdf': 'PDF', 'image': 'Изображение'}[kind]
        filename = (file.filename or '').replace('\\', '/').split('/')[-1][:180]
        suffix = Path(filename).suffix.lower()
        allowed = {'video': {'.mp4', '.mkv', '.mov', '.webm'}, 'pdf': {'.pdf'}, 'image': {'.png', '.jpg', '.jpeg', '.webp'}}[kind]
        if suffix not in allowed:
            raise HTTPException(422, {'field': field, 'message': f'{label}: неподдерживаемый формат.'})
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        asset_id = uid()
        path = directory / f'{asset_id}{suffix}'
        size = 0
        head = b''
        try:
            with path.open('xb') as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, {'field': field, 'message': f'{label}: файл превышает лимит {limit // 1024 // 1024} МБ.'})
                    if not head:
                        head = chunk[:32]
                    out.write(chunk)
            mime = None
            if kind == 'pdf' and head.startswith(b'%PDF-'):
                mime = 'application/pdf'
            elif kind == 'video' and ((suffix in {'.mp4', '.mov'} and head[4:8] == b'ftyp') or (suffix in {'.mkv', '.webm'} and head.startswith(b'\x1aE\xdf\xa3'))):
                mime = 'video/mp4' if suffix in {'.mp4', '.mov'} else 'video/webm'
            elif kind == 'image':
                if suffix == '.png' and head.startswith(b'\x89PNG\r\n\x1a\n'):
                    mime = 'image/png'
                elif suffix in {'.jpg', '.jpeg'} and head.startswith(b'\xff\xd8\xff'):
                    mime = 'image/jpeg'
                elif suffix == '.webp' and head.startswith(b'RIFF') and head[8:12] == b'WEBP':
                    mime = 'image/webp'
            if not size or not mime:
                raise HTTPException(422, {'field': field, 'message': f'{label}: содержимое не соответствует формату или файл пуст.'})
            return (asset_id, filename, mime, str(path), size)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post('/api/notes/{note_id}/assets', status_code=201)
    async def upload_image(note_id: str, file: UploadFile = File(), user_id=Depends(user)):
        with connect(settings) as db:
            note = entity(db, 'notes', note_id, user_id, write=True)
            course_id = note['course_id']
        item = await store_file(file, settings.data / 'assets', settings.max_image, 'image')
        try:
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                entity(db, 'notes', note_id, user_id, write=True)
                check_quota(db, item[4])
                db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)', (item[0], course_id, note_id, None, *item[1:]))
        except BaseException:
            Path(item[3]).unlink(missing_ok=True)
            raise
        return {'url': f'/api/assets/{item[0]}', 'id': item[0]}

    @app.get('/api/assets/{asset_id}')
    def asset(asset_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            row = entity(db, 'assets', asset_id, user_id)
        original = Path(row['path'])
        path = original.resolve()
        if any(p.is_symlink() for p in [original, *original.parents]) or not (path.is_relative_to(settings.data.resolve()) or path.is_relative_to(settings.jobs.resolve())) or not path.is_file():
            raise HTTPException(404, 'Файл недоступен.')
        return FileResponse(path, media_type=row['mime'], filename=row['name'], content_disposition_type='inline' if row['mime'].startswith('image/') else 'attachment')

    def check_quota(db, added=0):
        if db.execute('SELECT coalesce(sum(size),0) FROM assets').fetchone()[0] + added > 3 * 1024**3:
            raise HTTPException(507, 'Лимит локального хранилища 3 ГБ исчерпан.')

    @app.post('/api/jobs', status_code=201)
    async def create_job(metadata: str = Form(max_length=12000), video: UploadFile | None = File(default=None), pdf: UploadFile | None = File(default=None), user_id=Depends(user)):
        try:
            body = JobInput.model_validate_json(metadata)
        except ValueError:
            raise HTTPException(422, 'Проверьте параметры задания.')
        if not body.title.strip():
            raise HTTPException(422, 'Введите название конспекта.')
        with connect(settings) as db:
            access(db, body.courseId, user_id, write=True)
            count = db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0]
            if count >= 10:
                raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
            check_quota(db)
        if body.sourceType == 'url':
            if body.mode == 'real':
                raise HTTPException(422, 'Обработка URL отключена. Загрузите локальный видеофайл.')
            parsed = urlparse(body.sourceName)
            if parsed.scheme not in ['http', 'https'] or not parsed.hostname or parsed.username or parsed.password:
                raise HTTPException(422, 'Укажите корректную HTTP(S)-ссылку без логина и пароля.')
        elif not video:
            raise HTTPException(422, 'Повторно выберите видеофайл для загрузки.')
        if body.mode == 'real':
            from .worker import JobError, rpc
            try:
                status = await run_in_threadpool(rpc, settings, settings.provider_url(user_id), '/providers/status', {'userId': user_id}, timeout=25)
            except JobError:
                raise HTTPException(503, {'field': 'provider', 'message': 'Сервис подключения недоступен. Повторите попытку после запуска приложения.'}) from None
            if not any(p['id'] == body.provider and p['connected'] for p in status.get('providers', [])):
                raise HTTPException(422, {'field': 'provider', 'message': f'Войдите в {"Codex" if body.provider == "codex" else "Claude"} перед отправкой видео.'})
        job_id = uid()
        directory = settings.jobs / job_id
        assets = []
        try:
            payload = body.model_dump()
            payload['timestamps'] = False
            if video:
                item = await store_file(video, directory, settings.max_video, 'video')
                assets.append(item)
                payload.update(sourceName=item[1], sourceSize=item[4], videoPath=Path(item[3]).name, sourceAssetId=item[0])
            if pdf:
                item = await store_file(pdf, directory, settings.max_pdf, 'pdf')
                assets.append(item)
                payload.update(pdfName=item[1], pdfPath=Path(item[3]).name, pdfAssetId=item[0])
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                access(db, body.courseId, user_id, write=True)
                if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0] >= 10:
                    raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
                check_quota(db, sum(item[4] for item in assets))
                db.execute('INSERT INTO jobs(id,course_id,created_by,payload,status,created_at) VALUES (?,?,?,?,?,?)', (job_id, body.courseId, user_id, json.dumps(payload, ensure_ascii=False), 'queued', now()))
                for item in assets:
                    db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)', (item[0], body.courseId, None, job_id, *item[1:]))
            return {'id': job_id}
        except BaseException:
            import shutil
            if directory.exists():
                shutil.rmtree(directory)
            raise

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            return job_json(entity(db, 'jobs', job_id, user_id))

    @app.post('/api/jobs/{job_id}/{action}')
    def job_action(job_id: str, action: Literal['cancel', 'retry'], user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = entity(db, 'jobs', job_id, user_id, write=True)
            if row['created_by'] != user_id:
                raise HTTPException(403, 'Управлять заданием может его отправитель. Создайте собственное задание для своего аккаунта провайдера.')
            if action == 'cancel' and row['status'] == 'queued':
                db.execute("UPDATE jobs SET status='cancelled',completed_at=? WHERE id=? AND status='queued'", (now(), job_id))
            elif action == 'retry' and row['status'] == 'failed':
                if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0] >= 10:
                    raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
                db.execute("UPDATE jobs SET status='queued',created_at=?,started_at=NULL,completed_at=NULL,error=NULL,error_code=NULL,attempt=attempt+1 WHERE id=? AND status='failed'", (now(), job_id))
            else:
                raise HTTPException(409, 'Состояние задания уже изменилось.')
        return {'ok': True}

    @app.post('/api/import')
    def import_local(body: dict, user_id=Depends(user)):
        raw = json.dumps({'courses': body.get('courses', []), 'notes': body.get('notes', [])}, ensure_ascii=False, sort_keys=True)
        if len(raw.encode()) > 5 * 1024 * 1024:
            raise HTTPException(413, 'Библиотека превышает 5 МБ.')
        digest = hashlib.sha256(raw.encode()).hexdigest()
        courses = body.get('courses', [])
        notes = body.get('notes', [])
        if not isinstance(courses, list) or not isinstance(notes, list) or len(courses) > 100 or len(notes) > 500:
            raise HTTPException(422, 'Неверный формат локальной библиотеки.')
        with connect(settings) as db:
            if db.execute('SELECT 1 FROM imports WHERE user_id=? AND digest=?', (user_id, digest)).fetchone():
                return {'imported': 0}
            mapping = {}
            for c in courses:
                if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id'] or c['id'] in mapping:
                    raise HTTPException(422, 'Некорректный или повторный идентификатор курса.')
                try:
                    data = CourseInput(title=c.get('title', ''), workspace='personal')
                    if not data.title.strip():
                        raise ValueError()
                except ValueError:
                    raise HTTPException(422, 'Некорректное название импортируемого курса.') from None
                mapping[c['id']] = uid()
                db.execute('INSERT INTO courses VALUES (?,?,?,?,?)', (mapping[c['id']], data.title.strip(), 'personal', user_id, now()))
            for n in notes:
                if not isinstance(n, dict) or not isinstance(n.get('courseId'), str):
                    raise HTTPException(422, 'Некорректная импортируемая заметка.')
                try:
                    data = NoteInput(courseId=mapping.get(n.get('courseId'), ''), title=n.get('title', ''), markdown=n.get('markdown', ''))
                    if not data.title.strip():
                        raise ValueError()
                except ValueError:
                    raise HTTPException(422, 'Некорректное название или Markdown заметки.') from None
                if not data.courseId:
                    raise HTTPException(422, 'У заметки отсутствует курс.')
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?)', (uid(), data.courseId, data.title, data.markdown, 'generated', 1, now(), now(), None, 1))
            db.execute('INSERT INTO imports VALUES (?,?)', (user_id, digest))
        return {'imported': len(notes), 'courses': len(courses)}

    return app


app = create_app()
