import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
import threading
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError

from .config import Settings
from .db import (connect, course_json, initialize, job_json, note_asset_ids,
                 note_asset_reference, note_json, now, replace_note_asset_ids, uid)
from .media import (MAX_TRANSCRIPT_BYTES, Upload, discard_job_video,
                    index_matching_legacy_uploads, media_result, register_upload, regular_upload,
                    remove_asset, storage_usage, upload_charge, queue_file_cleanup)
from .note_export import filename as export_filename, localized_markdown, open_asset_fd, stream_archive
from .providers import CATALOG, MAX_NOTE_CHARS, PROVIDERS, ProviderId, valid_api_key, valid_options
from .auth import install_auth, public_user, email_address, throttle, consume_limit, PasswordInput, password_matches


class CourseInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    workspace: Literal['personal', 'shared'] = 'personal'


class NoteInput(BaseModel):
    category: str = Field(default="", max_length=250)
    courseId: str
    title: str = Field(default='Без названия', min_length=1, max_length=120)
    markdown: str = Field(default='', max_length=MAX_NOTE_CHARS)


class MarkdownDocument(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    markdown: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)


class MarkdownImport(BaseModel):
    courseId: str = Field(max_length=36)
    category: str = Field(default='', max_length=250)
    documents: list[MarkdownDocument] = Field(min_length=1, max_length=25)


class NoteEdit(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    markdown: str = Field(max_length=MAX_NOTE_CHARS)
    revision: int = Field(ge=1)


class NoteRestore(BaseModel):
    revision: int = Field(ge=1)


class JobInput(BaseModel):
    category: str = Field(default="", max_length=250)
    courseId: str
    title: str = Field(min_length=1, max_length=120)
    sourceType: Literal['file', 'url'] = 'file'
    sourceName: str = Field(default='', max_length=2048)
    preset: Literal['lecture', 'seminar'] = 'lecture'
    length: Literal['short', 'medium', 'detailed'] = 'medium'
    targetWords: int | None = Field(default=None, ge=100, le=5000)
    slideLimit: int | None = Field(default=6, ge=0, le=24)
    instructions: str = Field(default='', max_length=4000)
    mode: Literal['mock', 'real'] = 'real'
    language: Literal['ru', 'en', 'auto'] = 'ru'
    provider: ProviderId = 'codex'
    model: str = Field(default='', max_length=180)
    apiBase: str | None = Field(default=None, max_length=500)
    reuseJobId: str | None = Field(default=None, max_length=36)
    effort: Literal['low', 'medium', 'high', 'xhigh'] = 'medium'

    @model_validator(mode='after')
    def options(self):
        if self.model and not valid_options(self.provider, self.model, self.effort):
            raise ValueError('Unsupported model or effort')
        return self


class PinnedFileResponse(FileResponse):
    def __init__(self, fd, **options):
        self.fd = fd
        super().__init__(f'/proc/self/fd/{fd}', **options)

    async def __call__(self, scope, receive, send):
        try:
            # Use streaming I/O because pathsend may reopen the path after this response returns.
            extensions = {key: value for key, value in scope.get('extensions', {}).items() if key != 'http.response.pathsend'}
            await super().__call__({**scope, 'extensions': extensions}, receive, send)
        finally:
            os.close(self.fd)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(_app):
        if settings.production and settings.smtp_user and not Path(settings.smtp_password_file).is_file():
            raise RuntimeError('Configure SMTP secret before starting production')
        initialize(settings)
        # A crash during the external profile purge must not lock the account
        # permanently. DB erasure itself is transactional; an incomplete request
        # is retried explicitly by the owner after restart.
        with connect(settings) as db:
            db.execute('UPDATE users SET disabled=0 WHERE disabled=2')
        yield

    app = FastAPI(title='LectureVault', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['api', '127.0.0.1', 'localhost', urlparse(settings.public_url).hostname, *([] if settings.production else ['testserver'])])

    @app.middleware('http')
    async def boundaries(request: Request, call_next):
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            if request.headers.get('x-lecturevault') != '1':
                return JSONResponse({'detail': 'Отсутствует заголовок защиты запроса.'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin not in settings.origins:
                return JSONResponse({'detail': 'Недопустимый origin.'}, status_code=403)
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'detail': 'Неверный размер запроса.'}, status_code=400)
        limit = 6 * 1024 * 1024 if request.headers.get('content-type', '').startswith('application/json') else 565 * 1024 * 1024
        if length > limit:
            return JSONResponse({'detail': 'Запрос превышает допустимый размер.'}, status_code=413)
        public_auth = {'/api/auth/register', '/api/auth/login', '/api/auth/logout', '/api/auth/verify-email', '/api/auth/resend-verification', '/api/auth/forgot-password', '/api/auth/reset-password'}
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE'] and request.url.path not in public_auth:
            try:
                user(request)  # Reject unauthenticated uploads before multipart parsing/spooling.
            except HTTPException as error:
                return JSONResponse({'detail': error.detail}, status_code=error.status_code)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        field = next((str(e['loc'][-1]) for e in error.errors() if e.get('loc')), '')
        return JSONResponse({'detail': {'field': field, 'message': 'Проверьте введённые данные и допустимую длину полей.'}}, status_code=422)

    user, recent_user = install_auth(app, settings)
    account_locks = {}
    account_locks_guard = threading.Lock()

    def account_action(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            identity = kwargs['user_id']
            with account_locks_guard:
                lock = account_locks.setdefault(identity, threading.RLock())
            with lock:
                with connect(settings) as db:
                    if not db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0', (identity,)).fetchone():
                        raise HTTPException(401, 'Войдите в аккаунт.')
                return function(*args, **kwargs)
        return wrapped

    def access(db, course_id, user_id, write=False, owner=False):
        if not db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0', (user_id,)).fetchone():
            raise HTTPException(401, 'Войдите в аккаунт.')
        course = db.execute('SELECT c.* FROM courses c JOIN users u ON u.id=c.owner_id AND u.disabled=0 WHERE c.id=?', (course_id,)).fetchone()
        role = None
        if course:
            if course['owner_id'] == user_id:
                role = 'owner'
            elif course['workspace'] == 'shared':
                member = db.execute('SELECT role FROM members WHERE course_id=? AND user_id=?', (course_id, user_id)).fetchone()
                if member:
                    role = member['role']
        if not role:
            raise HTTPException(404, 'Категория не найдена или недоступна.')
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
        return {'ok': True}

    def job_state(row):
        result = job_json(row)
        result['canRetry'] = row['status'] == 'failed' and result.get('videoArchive') not in ('queued', 'processing') and (
            result.get('mode') == 'mock' or bool(result.get('transcriptAssetId') or
                                                (result.get('videoPath') and not result.get('temporaryVideo'))))
        for internal in ('apiBase', 'sourceSha256', 'temporaryVideo', 'videoArchive', 'videoArchiveError'):
            result.pop(internal, None)
        if row['status'] == 'transcribing' and result.get('mode') == 'real':
            marker = settings.jobs / row['id'] / 'recognition.progress'
            try:
                with os.fdopen(os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as file:
                    if stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                        value = int(file.read(4))
                        if 0 <= value <= 100:
                            result['recognitionPercent'] = value
            except (OSError, ValueError):
                pass
        return result

    @app.get('/api/state')
    def state(user_id=Depends(user)):
        with connect(settings) as db:
            courses = []
            for row in db.execute("SELECT c.*, ci.icon FROM courses c LEFT JOIN course_icons ci ON ci.course_id=c.id JOIN users u ON u.id=c.owner_id AND u.disabled=0 WHERE c.owner_id=? OR (c.workspace='shared' AND c.id IN (SELECT course_id FROM members WHERE user_id=?)) ORDER BY c.created_at", (user_id, user_id)):
                try:
                    _, role = access(db, row['id'], user_id)
                    courses.append({**course_json(row, role), 'icon': row['icon'] or 'folder'})
                except HTTPException:
                    continue
            ids = [c['id'] for c in courses]
            marks = ','.join('?' for _ in ids) or 'NULL'
            by_id = {c['id']: c for c in courses}
            for row in db.execute(f'SELECT course_id,path,icon FROM category_folders WHERE course_id IN ({marks}) ORDER BY path', ids):
                root = by_id[row['course_id']]
                root.setdefault('folders', []).append(row['path'])
                if row['icon'] != 'folder':
                    root.setdefault('folderIcons', {})[row['path']] = row['icon']
            notes = [dict(id=row['id'], courseId=row['course_id'], category=row['path'] or '', title=row['title'], markdown='', contentLoaded=False, origin=row['origin'], revision=row['revision'], createdAt=row['created_at'], updatedAt=row['updated_at'], jobId=row['job_id'], isMock=bool(row['is_mock']), createdBy=row['created_by']) for row in db.execute(f'SELECT id,course_id,title,origin,revision,created_at,updated_at,job_id,is_mock,created_by,path FROM notes LEFT JOIN note_categories ON note_categories.note_id=notes.id WHERE course_id IN ({marks}) ORDER BY created_at', ids)]
            jobs = [job_state(row) for row in db.execute(f'SELECT * FROM jobs WHERE course_id IN ({marks}) ORDER BY created_at', ids)]
        return {'version': 1, 'courses': courses, 'notes': notes, 'jobs': jobs}

    @app.get('/api/providers')
    def providers(refresh: bool = False, user_id=Depends(user)):
        throttle(settings, 'providers-status:' + user_id, 90, 60)
        if refresh:
            throttle(settings, 'providers-refresh:' + user_id, 6, 60)
        from .worker import JobError, rpc
        try:
            return rpc(settings, settings.provider_url(user_id), '/providers/status', {'userId': user_id, **({'refresh': True} if refresh else {})}, timeout=35)
        except JobError as error:
            if refresh:
                raise HTTPException(409 if error.code == 'CATALOG_BUSY' else 503, error.message) from None
            return {'providers': [{**p, 'connected': False, 'login': {'status': 'unavailable'}} for p in CATALOG]}

    @app.post('/api/providers/{provider}/{action}')
    @account_action
    def provider_action(provider: ProviderId, action: Literal['connect', 'key', 'disconnect'], body: dict, user_id=Depends(recent_user)):
        from .worker import JobError, rpc
        payload = {'userId': user_id, 'provider': provider}
        throttle(settings, 'provider-action:' + user_id, 20, 900)
        if action == 'key':
            key = body.get('apiKey')
            if not valid_api_key(provider, key):
                raise HTTPException(422, 'Введите API-ключ выбранного провайдера.')
            payload['apiKey'] = key
            for option in ('baseUrl', 'workspace'):
                if option in body:
                    payload[option] = body[option]
        # A broker call may succeed even when its reply is lost.
        if action in ['connect', 'key']:
            with connect(settings) as db:
                db.execute('INSERT OR IGNORE INTO provider_links VALUES (?)', (user_id,))
        try:
            return rpc(settings, settings.provider_url(user_id), f'/providers/{action}', payload, timeout=30)
        except JobError as error:
            raise HTTPException(502, error.message) from None

    @app.post('/api/courses', status_code=201)
    def create_course(body: CourseInput, user_id=Depends(user)):
        title = body.title.strip()
        if not title:
            raise HTTPException(422, 'Введите название категории.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            course_id = insert_course(db, title, body.workspace, user_id)
        return {'id': course_id}

    def insert_course(db, title, workspace, user_id):
        titles = db.execute('SELECT title FROM courses WHERE owner_id=?', (user_id,)).fetchall()
        if len(titles) >= 100:
            raise HTTPException(422, 'Не более 100 собственных категорий.')
        if any(row['title'].casefold() == title.casefold() for row in titles):
            raise HTTPException(409, 'В библиотеке уже есть категория с таким названием.')
        course_id = uid()
        db.execute('INSERT INTO courses VALUES (?,?,?,?,?)', (course_id, title, workspace, user_id, now()))
        return course_id

    @app.post('/api/courses/{course_id}/invite')
    def invite_link(course_id: str, body: dict, user_id=Depends(user)):
        role = body.get('role', 'viewer')
        if role not in ('viewer', 'editor'):
            raise HTTPException(422, 'Выберите роль участника.')
        throttle(settings, 'invite:' + user_id, 30, 3600)
        token = secrets.token_urlsafe(32)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, course_id, user_id, owner=True)
            db.execute('INSERT OR REPLACE INTO course_invites VALUES (?,?,?,?)',
                       (course_id, hashlib.sha256(token.encode()).hexdigest(), role, now() + 86400000))
        return {'token': token}

    @app.get('/api/courses/{course_id}/invite')
    def invite_status(course_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            access(db, course_id, user_id, owner=True)
            row = db.execute('SELECT role FROM course_invites WHERE course_id=? AND expires>?', (course_id, now())).fetchone()
        return {'active': bool(row), 'role': row['role'] if row else None}

    @app.delete('/api/courses/{course_id}/invite')
    def revoke_invite(course_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, course_id, user_id, owner=True)
            db.execute('DELETE FROM course_invites WHERE course_id=?', (course_id,))
        return {'ok': True}

    @app.post('/api/invitations/{action}')
    def invitation(action: Literal['preview', 'accept'], body: dict, user_id=Depends(user)):
        token = body.get('token')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise HTTPException(404, 'Приглашение недействительно.')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT i.*, c.title, c.owner_id FROM course_invites i JOIN courses c ON c.id=i.course_id JOIN users u ON u.id=c.owner_id WHERE i.digest=? AND i.expires>? AND u.disabled=0', (digest, now())).fetchone()
            if not row:
                raise HTTPException(404, 'Ссылка истекла, отозвана или уже использована.')
            if action == 'accept' and user_id != row['owner_id']:
                member = db.execute('SELECT role FROM members WHERE course_id=? AND user_id=?', (row['course_id'], user_id)).fetchone()
                if not member and db.execute('SELECT count(*) FROM members WHERE course_id=?', (row['course_id'],)).fetchone()[0] >= 100:
                    raise HTTPException(422, 'В категории уже 100 участников.')
                db.execute("UPDATE courses SET workspace='shared' WHERE id=?", (row['course_id'],))
                if not member:
                    db.execute('INSERT INTO members VALUES (?,?,?)', (row['course_id'], user_id, row['role']))
                elif row['role'] == 'editor' and member['role'] == 'viewer':
                    db.execute("UPDATE members SET role='editor' WHERE course_id=? AND user_id=?", (row['course_id'], user_id))
                db.execute('DELETE FROM course_invites WHERE digest=?', (digest,))
            return {'id': row['course_id'], 'title': row['title'], 'role': row['role']}

    @app.get('/api/courses/{course_id}/members')
    def members(course_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            course, _ = access(db, course_id, user_id)
            rows = db.execute("SELECT u.*,CASE WHEN u.id=? THEN 'owner' ELSE m.role END AS role FROM users u LEFT JOIN members m ON m.user_id=u.id AND m.course_id=? WHERE u.id=? OR m.user_id IS NOT NULL", (course['owner_id'], course_id, course['owner_id'])).fetchall()
        return [{**public_user(row), 'role': row['role']} for row in rows]

    @app.post('/api/courses/{course_id}/members')
    def add_member(course_id: str, body: dict, user_id=Depends(user)):
        email = email_address(body.get('email', ''))
        role = body.get('role', 'viewer')
        if role not in ['editor', 'viewer']:
            raise HTTPException(422, 'Выберите роль участника.')
        throttle(settings, 'invite:' + user_id, 30, 3600)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            course, _ = access(db, course_id, user_id, owner=True)
            member = db.execute('SELECT * FROM users WHERE email=? AND verified=1 AND disabled=0', (email,)).fetchone()
            if not member:
                raise HTTPException(404, 'Не удалось добавить участника. Проверьте email: пользователь должен зарегистрироваться и подтвердить его.')
            if member['id'] == user_id:
                raise HTTPException(409, 'Вы уже владелец категории.')
            if db.execute('SELECT 1 FROM members WHERE course_id=? AND user_id=?', (course_id, member['id'])).fetchone():
                raise HTTPException(409, 'Участник уже добавлен.')
            if db.execute('SELECT count(*) FROM members WHERE course_id=?', (course_id,)).fetchone()[0] >= 100:
                raise HTTPException(422, 'Не более 100 участников категории.')
            db.execute("UPDATE courses SET workspace='shared' WHERE id=?", (course_id,))
            db.execute('INSERT INTO members VALUES (?,?,?)', (course_id, member['id'], role))
        return {'ok': True}

    @app.put('/api/courses/{course_id}/members/{member_id}')
    def membership(course_id: str, member_id: str, body: dict, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            course, _ = access(db, course_id, user_id, owner=True)
            if course['workspace'] != 'shared' or member_id == course['owner_id'] or body.get('role') not in ['editor', 'viewer', 'none']:
                raise HTTPException(422, 'Недопустимое изменение роли.')
            if not db.execute('SELECT 1 FROM members WHERE course_id=? AND user_id=?', (course_id, member_id)).fetchone():
                raise HTTPException(404, 'Участник не найден.')
            db.execute('DELETE FROM members WHERE course_id=? AND user_id=?', (course_id, member_id))
            if body['role'] != 'editor':
                cancelled = db.execute("""SELECT * FROM jobs WHERE course_id=? AND created_by=? AND status='queued'
                    AND json_extract(payload,'$.temporaryVideo')=1""", (course_id, member_id)).fetchall()
                db.execute("""UPDATE jobs SET
                    completed_at=CASE WHEN status IN ('queued','waiting_generation') THEN ? ELSE completed_at END,
                    status=CASE WHEN status IN ('queued','waiting_generation') THEN 'cancelled' ELSE status END,
                    error_code='ACCESS_REVOKED',error='Права на запись в категорию отозваны.'
                    WHERE course_id=? AND created_by=? AND status NOT IN ('failed','ready','cancelled')""",
                    (now(), course_id, member_id))
                for row in cancelled:
                    discard_job_video(db, settings, job_json(row))
            if body['role'] != 'none':
                db.execute('INSERT INTO members VALUES (?,?,?)', (course_id, member_id, body['role']))
        return {'ok': True}

    @app.post('/api/notes', status_code=201)
    def create_note(body: NoteInput, user_id=Depends(user)):
        if not body.title.strip():
            raise HTTPException(422, 'Введите название заметки.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, body.courseId, user_id, write=True)
            check_folder(db, body.courseId, body.category)
            check_note_quota(db, body.courseId, body.markdown)
            note_id = insert_document(db, body.courseId, body.category, body.title.strip(), body.markdown, user_id)
            result = note_json(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())
        return result

    def insert_document(db, course_id, category, title, markdown, user_id):
        note_id = uid()
        timestamp = now()
        db.execute("""INSERT INTO notes(id,course_id,title,markdown,origin,created_at,updated_at,created_by,updated_by)
                    VALUES (?,?,?,?,'manual',?,?,?,?)""",
                   (note_id, course_id, title, markdown, timestamp, timestamp, user_id, user_id))
        if category:
            db.execute('INSERT INTO note_categories VALUES (?,?)', (note_id, category))
        return note_id

    @app.post('/api/notes/import-markdown', status_code=201)
    def import_markdown(body: MarkdownImport, user_id=Depends(user)):
        if any(not doc.title.strip() or not doc.markdown.strip() or '\x00' in doc.markdown or
               any(ord(char) < 32 for char in doc.title) for doc in body.documents):
            raise HTTPException(422, 'Каждой заметке нужны название и непустой текст Markdown.')
        try:
            size = sum(len(doc.markdown.encode('utf-8')) for doc in body.documents)
            for doc in body.documents:
                doc.title.encode('utf-8')
        except UnicodeEncodeError:
            raise HTTPException(422, 'Файлы должны быть в кодировке UTF-8.') from None
        if size > 5 * 1024 * 1024:
            raise HTTPException(413, 'Общий размер Markdown-файлов превышает 5 МБ.')
        output = []
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, body.courseId, user_id, write=True)
            check_folder(db, body.courseId, body.category)
            consume_limit(db, 'markdown-import:' + user_id, 30, 3600)
            for doc in body.documents:
                title = doc.title.strip()
                existing = db.execute("""SELECT n.id FROM notes n LEFT JOIN note_categories nc ON nc.note_id=n.id
                    WHERE n.course_id=? AND coalesce(nc.path,'')=? AND n.title=? AND n.markdown=?
                    ORDER BY n.rowid LIMIT 1""", (body.courseId, body.category, title, doc.markdown)).fetchone()
                if existing:
                    output.append({'id': existing['id'], 'title': title, 'status': 'existing'})
                else:
                    check_note_quota(db, body.courseId, doc.markdown)
                    note_id = insert_document(db, body.courseId, body.category, title, doc.markdown, user_id)
                    output.append({'id': note_id, 'title': title, 'status': 'imported'})
        return {'documents': output}

    @app.get('/api/notes/{note_id}')
    def get_note(note_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            return note_json(entity(db, 'notes', note_id, user_id))

    def note_for_edit(db, note_id, user_id):
        note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
        if not note:
            raise HTTPException(404, 'Материал не найден.')
        course, role = access(db, note['course_id'], user_id, write=True)
        return note, course, role

    def update_note(db, note, course, role, user_id, title, markdown, revision):
        if note['revision'] != revision:
            raise HTTPException(409, 'Документ уже изменён. Откройте актуальную версию и повторите действие.')
        if note['title'] == title and note['markdown'] == markdown:
            return note_json(note)

        note_id = note['id']
        previous_assets = note_asset_ids(note['markdown'])
        if role != 'owner':
            removed = previous_assets - note_asset_ids(markdown)
            if removed and db.execute("""SELECT 1 FROM assets WHERE course_id=?
                AND coalesce(created_by,?)!=? AND id IN (SELECT value FROM json_each(?))""",
                (note['course_id'], course['owner_id'], user_id, json.dumps(sorted(removed)))).fetchone():
                raise HTTPException(403, 'Удалить вложение из заметки может его автор или владелец категории.')

        check_note_quota(db, note['course_id'], markdown, replacing=note_id)
        editor = note['updated_by'] or (note['created_by'] if revision == 1 else None)
        size = len(note['title'].encode()) + len(note['markdown'].encode())
        db.execute('INSERT INTO note_revisions VALUES (?,?,?,?,?,?,?)',
                   (note_id, revision, note['title'], note['markdown'], note['updated_at'], editor, size))
        db.executemany('INSERT INTO note_revision_assets VALUES (?,?,?)',
                       [(note_id, revision, asset_id) for asset_id in previous_assets])
        db.execute('DELETE FROM note_revisions WHERE note_id=? AND revision<=?', (note_id, revision - 20))
        db.execute("""DELETE FROM note_revisions WHERE rowid IN (
            SELECT id FROM (
                SELECT r.rowid AS id, sum(r.size) OVER (ORDER BY r.rowid DESC) AS retained_size
                FROM note_revisions r JOIN notes n ON n.id=r.note_id JOIN courses c ON c.id=n.course_id
                WHERE c.owner_id=?
            ) WHERE retained_size>?
        )""", (course['owner_id'], 25 * 1024 * 1024))
        db.execute('UPDATE notes SET title=?,markdown=?,updated_at=?,updated_by=?,revision=revision+1 WHERE id=?',
                   (title, markdown, now(), user_id, note_id))
        return note_json(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())

    @app.patch('/api/notes/{note_id}')
    def save_note(note_id: str, body: NoteEdit, user_id=Depends(user)):
        title = body.title.strip()
        if not title:
            raise HTTPException(422, 'Введите название заметки.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            note, course, role = note_for_edit(db, note_id, user_id)
            return update_note(db, note, course, role, user_id, title, body.markdown, body.revision)

    @app.get('/api/notes/{note_id}/revisions')
    def list_note_revisions(note_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN')
            note, _, _ = note_for_edit(db, note_id, user_id)
            versions = db.execute("""SELECT r.revision,r.title,r.updated_at,u.name AS editor
                FROM note_revisions r LEFT JOIN users u ON u.id=r.edited_by AND u.disabled=0
                WHERE r.note_id=? ORDER BY r.revision DESC""", (note_id,))
            return {'revision': note['revision'], 'versions': [
                dict(revision=row['revision'], title=row['title'], updatedAt=row['updated_at'], editor=row['editor'])
                for row in versions
            ]}

    def find_note_revision(db, note, revision):
        if not 1 <= revision < note['revision']:
            raise HTTPException(404, 'Версия заметки недоступна.')
        row = db.execute('SELECT title,markdown FROM note_revisions WHERE note_id=? AND revision=?',
                         (note['id'], revision)).fetchone()
        if not row:
            raise HTTPException(404, 'Версия заметки недоступна.')
        return row

    @app.get('/api/notes/{note_id}/revisions/{revision}')
    def get_note_revision(note_id: str, revision: int, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN')
            note, _, _ = note_for_edit(db, note_id, user_id)
            return dict(find_note_revision(db, note, revision))

    @app.post('/api/notes/{note_id}/revisions/{revision}/restore')
    def restore_note_revision(note_id: str, revision: int, body: NoteRestore, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            note, course, role = note_for_edit(db, note_id, user_id)
            prior = find_note_revision(db, note, revision)
            return update_note(db, note, course, role, user_id, prior['title'], prior['markdown'], body.revision)

    def folder_parts(path):
        if not isinstance(path, str) or len(path) > 250 or any(ord(c) < 32 for c in path):
            raise HTTPException(422, 'Введите название категории длиной до 250 символов.')
        parts = [part.strip() for part in path.split('/')] if path.strip() else []
        if len(parts) > 6 or any(not part or len(part) > 60 or part in ('.', '..') for part in parts):
            raise HTTPException(422, 'До 6 вложенных уровней, до 60 символов в названии.')
        return parts

    def check_folder(db, root, path):
        if path and not db.execute('SELECT 1 FROM category_folders WHERE course_id=? AND path=?', (root, path)).fetchone():
            raise HTTPException(404, 'Категория не найдена.')

    def ensure_folders(db, root, parts):
        for depth in range(1, len(parts) + 1):
            path = '/'.join(parts[:depth])
            if db.execute('SELECT 1 FROM category_folders WHERE course_id=? AND path=?', (root, path)).fetchone():
                continue
            if db.execute('SELECT count(*) FROM category_folders WHERE course_id=?', (root,)).fetchone()[0] >= 500:
                raise HTTPException(409, 'Достигнут предел вложенных категорий.')
            db.execute('INSERT INTO category_folders(course_id,path) VALUES (?,?)', (root, path))

    @app.post('/api/courses/{course_id}/folders', status_code=201)
    def create_folder(course_id: str, body: dict, user_id=Depends(user)):
        name, parent = body.get('name'), body.get('parent', '')
        if not isinstance(name, str) or not name.strip() or '/' in name or not isinstance(parent, str):
            raise HTTPException(422, 'Введите название без косой черты.')
        parts = folder_parts('/'.join(filter(None, [parent, name])))
        path = '/'.join(parts)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, course_id, user_id, write=True)
            check_folder(db, course_id, parent)
            if db.execute('SELECT 1 FROM category_folders WHERE course_id=? AND path=?', (course_id, path)).fetchone():
                raise HTTPException(409, 'Категория с таким названием уже существует.')
            ensure_folders(db, course_id, parts)
        return {'path': path}

    @app.patch('/api/courses/{course_id}/folders')
    def reorganize_folder(course_id: str, body: dict, user_id=Depends(user)):
        path = body.get('path', '')
        remove_level = body.get('operation') == 'remove-level'
        if body.get('operation') not in ('rename', 'remove-level') or not isinstance(path, str):
            raise HTTPException(422, 'Выберите действие и категорию.')
        name = body.get('name')
        if not remove_level and (not isinstance(name, str) or not name.strip() or (path and '/' in name)):
            raise HTTPException(422, 'Введите корректное название категории.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            course, _ = access(db, course_id, user_id, owner=True)
            if not path:
                if remove_level or len(name.strip()) > 80:
                    raise HTTPException(422, 'Введите название длиной до 80 символов.')
                title = name.strip()
                titles = db.execute('SELECT title FROM courses WHERE owner_id=? AND id!=?', (course['owner_id'], course_id))
                if any(row['title'].casefold() == title.casefold() for row in titles):
                    raise HTTPException(409, 'Категория с таким названием уже существует.')
                db.execute('UPDATE courses SET title=? WHERE id=?', (title, course_id))
                return {'path': ''}
            check_folder(db, course_id, path)
            parent = path.rpartition('/')[0]
            destination = parent if remove_level else '/'.join(filter(None, [parent, name.strip()]))
            if destination == path:
                return {'path': path}
            existing = {r['path'] for r in db.execute('SELECT path FROM category_folders WHERE course_id=?', (course_id,))}
            descendants = sorted((p for p in existing if p == path or p.startswith(path + '/')), key=len)
            mapping = {old: '/'.join(filter(None, [destination, old[len(path):].lstrip('/')])) for old in descendants}
            others = existing.difference(descendants)
            for old, new in mapping.items():
                if remove_level and old == path:
                    continue
                folder_parts(new)
                if new in others:
                    raise HTTPException(409, 'В родительской категории уже есть подкатегория с таким названием.')
            # Process parents first when lifting a level, including repeated names A/A.
            for old, new in mapping.items():
                if remove_level and old == path:
                    db.execute('DELETE FROM category_folders WHERE course_id=? AND path=?', (course_id, old))
                else:
                    db.execute('UPDATE category_folders SET path=? WHERE course_id=? AND path=?', (new, course_id, old))
                members = 'path=? AND note_id IN (SELECT id FROM notes WHERE course_id=?)'
                if new:
                    db.execute(f'UPDATE note_categories SET path=? WHERE {members}', (new, old, course_id))
                else:
                    db.execute(f'DELETE FROM note_categories WHERE {members}', (old, course_id))
                db.execute("""UPDATE jobs SET payload=json_set(payload,'$.category',?)
                              WHERE course_id=? AND json_extract(payload,'$.category')=?""", (new, course_id, old))
        return {'path': destination}

    def inside_branch(path, branch):
        return not branch or path == branch or path.startswith(branch + '/')

    def category_access_confirmation(db, source, target, path, parent):
        def sharing(course):
            if course['workspace'] != 'shared':
                return [], None
            members = db.execute('SELECT user_id,role FROM members WHERE course_id=? ORDER BY user_id', (course['id'],))
            invite = db.execute('SELECT digest FROM course_invites WHERE course_id=? AND expires>?', (course['id'], now())).fetchone()
            return [tuple(member) for member in members], invite['digest'] if invite else None

        source_members, source_invite = sharing(source)
        target_members, target_invite = sharing(target)
        access_changes = source['workspace'] != target['workspace'] or source_members != target_members or target_invite
        invite_ends = source_invite and not path
        if not access_changes and not invite_ends:
            return None
        messages = []
        if access_changes:
            messages.append('Перемещённая ветка получит права доступа категории назначения.')
        if invite_ends:
            messages.append('Пригласительная ссылка исходной категории перестанет действовать.')
        snapshot = (source['id'], path, target['id'], parent, source['workspace'], target['workspace'],
                    source_members, target_members, source_invite, target_invite)
        return {
            'code': 'CATEGORY_ACCESS_CHANGE',
            'message': ' '.join(messages),
            'confirmation': hashlib.sha256(json.dumps(snapshot).encode()).hexdigest(),
        }

    @app.put('/api/courses/{course_id}/location')
    def move_category(course_id: str, body: dict, user_id=Depends(user)):
        path = '/'.join(folder_parts(body.get('path', '')))
        parent = '/'.join(folder_parts(body.get('parent', '')))
        target_id = body.get('courseId')
        promote = 'courseId' in body and target_id is None
        if not promote and (not isinstance(target_id, str) or not target_id):
            raise HTTPException(422, 'Выберите категорию назначения.')
        if promote and (not path or parent):
            raise HTTPException(422, 'Выберите вложенную категорию для переноса в корень библиотеки.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            source, _ = access(db, course_id, user_id, owner=True)
            check_folder(db, course_id, path)
            name = path.rsplit('/', 1)[-1] if path else source['title']
            if promote:
                target_id = insert_course(db, name, source['workspace'], user_id)
                if source['workspace'] == 'shared':
                    db.execute('INSERT INTO members SELECT ?,user_id,role FROM members WHERE course_id=?', (target_id, course_id))
            target, _ = access(db, target_id, user_id, owner=True)
            check_folder(db, target_id, parent)
            same_course = target_id == course_id
            if same_course and inside_branch(parent, path):
                raise HTTPException(422, 'Категорию нельзя вложить в саму себя или в её подкатегорию.')
            if '/' in name:
                raise HTTPException(422, 'Перед переносом уберите косую черту из названия категории.')
            destination = '' if promote else '/'.join(filter(None, [parent, name]))
            if same_course and destination == path:
                return {'courseId': target_id, 'path': path}

            folders = {row['path']: row['icon'] for row in db.execute(
                'SELECT path,icon FROM category_folders WHERE course_id=?', (course_id,)) if inside_branch(row['path'], path)}
            if not path:
                icon = db.execute('SELECT icon FROM course_icons WHERE course_id=?', (course_id,)).fetchone()
                folders[''] = icon['icon'] if icon else 'folder'
            target_paths = {row['path'] for row in db.execute('SELECT path FROM category_folders WHERE course_id=?', (target_id,))}
            if same_course:
                target_paths.difference_update(folders)
            if len(target_paths) + len(folders) - int(promote) > 500:
                raise HTTPException(409, 'Достигнут предел вложенных категорий.')
            locations = {old: '/'.join(filter(None, [destination, old[len(path):].lstrip('/')])) for old in folders}
            for new in locations.values():
                folder_parts(new)
                if new in target_paths:
                    raise HTTPException(409, 'В категории назначения уже есть подкатегория с таким названием.')

            note_paths = {row['id']: row['category'] for row in db.execute(
                """SELECT n.id,coalesce(c.path,'') AS category FROM notes n
                    LEFT JOIN note_categories c ON c.note_id=n.id WHERE n.course_id=?""", (course_id,))}
            moving_notes = {note_id: locations.get(old, destination) for note_id, old in note_paths.items()
                            if not path or old in locations}
            jobs = [(row, json.loads(row['payload'])) for row in db.execute(
                'SELECT id,status,note_id,payload,created_by FROM jobs WHERE course_id=?', (course_id,))]
            writers = {target['owner_id']}
            if not same_course and target['workspace'] == 'shared':
                writers.update(row['user_id'] for row in db.execute(
                    "SELECT user_id FROM members WHERE course_id=? AND role='editor'", (target_id,)))
            moves = []
            for row, payload in jobs:
                old = note_paths.get(row['note_id'], payload.get('category', ''))
                if not path or old in locations:
                    if not same_course:
                        if row['status'] in ('acquiring', 'transcribing', 'preparing', 'generating') or payload.get('videoArchive') == 'processing':
                            raise HTTPException(409, 'В этой ветке обрабатываются материалы. Повторите перенос после завершения обработки.')
                        if row['status'] in ('queued', 'waiting_generation') and row['created_by'] not in writers:
                            raise HTTPException(409, 'Автор задания в очереди не может писать в категорию назначения. Дождитесь завершения задания.')
                    moves.append((row, payload, locations.get(old, destination)))

            replacements = {}
            if not same_course:
                confirmation = category_access_confirmation(db, source, target, path, parent)
                if confirmation and body.get('confirmation') != confirmation['confirmation']:
                    raise HTTPException(409, confirmation)
                if path:
                    replacements = move_branch_assets(db, course_id, target_id, moving_notes, jobs, moves)
                else:
                    db.execute('UPDATE assets SET course_id=? WHERE course_id=?', (target_id, course_id))

            db.executemany('DELETE FROM category_folders WHERE course_id=? AND path=?',
                           ((course_id, old) for old in folders if old))
            db.executemany('INSERT INTO category_folders(course_id,path,icon) VALUES (?,?,?)',
                           ((target_id, new, folders[old]) for old, new in locations.items() if new))
            if promote and folders[path] != 'folder':
                db.execute('INSERT INTO course_icons VALUES (?,?)', (target_id, folders[path]))
            db.executemany('DELETE FROM note_categories WHERE note_id=?',
                           ((note_id,) for note_id, new in moving_notes.items() if not new))
            db.executemany('INSERT OR REPLACE INTO note_categories VALUES (?,?)',
                           ((note_id, new) for note_id, new in moving_notes.items() if new))
            if not same_course:
                if path:
                    db.executemany('UPDATE notes SET course_id=? WHERE id=?', ((target_id, note_id) for note_id in moving_notes))
                else:
                    db.execute('UPDATE notes SET course_id=? WHERE course_id=?', (target_id, course_id))
            for row, payload, new in moves:
                payload.update(courseId=target_id, category=new)
                for key in job_asset_keys:
                    if payload.get(key) in replacements:
                        payload[key] = replacements[payload[key]]
                db.execute('UPDATE jobs SET course_id=?,payload=? WHERE id=?', (target_id, json.dumps(payload), row['id']))
            if not path:
                db.execute('DELETE FROM members WHERE course_id=?', (course_id,))
                db.execute('DELETE FROM courses WHERE id=?', (course_id,))
        return {'courseId': target_id, 'path': destination}

    @app.put('/api/notes/{note_id}/category')
    def move_note(note_id: str, body: dict, user_id=Depends(user)):
        parts = folder_parts(body.get('path'))
        path = '/'.join(parts)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            note, source, source_role = note_for_edit(db, note_id, user_id)
            target_id = body.get('courseId', source['id'])
            if not isinstance(target_id, str) or not target_id:
                raise HTTPException(422, 'Выберите категорию.')
            if target_id != source['id']:
                if source_role != 'owner' and note['created_by'] != user_id:
                    raise HTTPException(403, 'Перенести заметку в другую категорию может её автор или владелец исходной категории.')
                target, target_role = access(db, target_id, user_id, write=True)
                check_folder(db, target_id, path)
                transfer_note(db, note, source, target, target_role, path, user_id)
            elif body.get('existingOnly'):
                check_folder(db, target_id, path)
            else:
                ensure_folders(db, target_id, parts)
            if target_id == source['id'] and note['job_id']:
                db.execute("""UPDATE jobs SET payload=json_set(payload,'$.category',?) WHERE id=? AND course_id=? AND note_id=?""",
                           (path, note['job_id'], target_id, note_id))
            db.execute('DELETE FROM note_categories WHERE note_id=?', (note_id,))
            if parts:
                db.execute('INSERT INTO note_categories VALUES (?,?)', (note_id, path))
        return {'ok': True}

    def asset_used_elsewhere(db, asset_id, source_id, note_id, job_id):
        for row in db.execute('SELECT markdown FROM notes WHERE course_id=? AND id!=? AND instr(markdown,?)>0',
                              (source_id, note_id, '/api/assets/' + asset_id)):
            if asset_id in note_asset_ids(row['markdown']):
                return True
        return db.execute("""SELECT 1 FROM note_revision_assets ra JOIN notes n ON n.id=ra.note_id
            WHERE n.course_id=:course AND n.id!=:note AND ra.asset_id=:asset
            UNION ALL
            SELECT 1 FROM jobs WHERE course_id=:course AND (:job IS NULL OR id!=:job) AND
             (json_extract(payload,'$.sourceAssetId')=:asset OR json_extract(payload,'$.pdfAssetId')=:asset
              OR json_extract(payload,'$.transcriptAssetId')=:asset) LIMIT 1""",
                          dict(course=source_id, note=note_id, job=job_id, asset=asset_id)).fetchone() is not None

    job_asset_keys = ('sourceAssetId', 'pdfAssetId', 'transcriptAssetId')

    def copy_asset_reference(db, asset, target_id, note_id, job_id, uploader):
        copy_id = uid()
        db.execute("""INSERT INTO assets(id,course_id,note_id,job_id,name,mime,path,size,sha256,
            transcript_language,legacy_source_sha256,created_by)
            SELECT ?,?,?,?,name,mime,path,size,sha256,transcript_language,legacy_source_sha256,?
            FROM assets WHERE id=?""", (copy_id, target_id, note_id, job_id, uploader, asset['id']))
        db.execute("""INSERT INTO transcript_recognition SELECT ?,audio_sha256,language,engine
            FROM transcript_recognition WHERE asset_id=?""", (copy_id, asset['id']))
        return copy_id

    def move_branch_assets(db, source_id, target_id, moving_notes, jobs, moves):
        source_jobs = {row['id'] for row, _ in jobs}
        moving_jobs = {row['id'] for row, _, _ in moves}
        inside_assets, outside_assets = set(), set()
        for row, payload in jobs:
            refs = inside_assets if row['id'] in moving_jobs else outside_assets
            refs.update(payload[key] for key in job_asset_keys if payload.get(key))
        notes = db.execute("SELECT id,markdown FROM notes WHERE course_id=? AND instr(markdown,'/api/assets/')>0",
                           (source_id,)).fetchall()
        for row in notes:
            refs = inside_assets if row['id'] in moving_notes else outside_assets
            refs.update(note_asset_ids(row['markdown']))
        versions = db.execute("""SELECT ra.note_id,ra.asset_id FROM note_revision_assets ra
            JOIN notes n ON n.id=ra.note_id WHERE n.course_id=?""", (source_id,))
        for row in versions:
            refs = inside_assets if row['note_id'] in moving_notes else outside_assets
            refs.add(row['asset_id'])

        replacements = {}
        assets = db.execute('SELECT * FROM assets WHERE course_id=?', (source_id,)).fetchall()
        for asset in assets:
            asset_id, note_id, job_id = asset['id'], asset['note_id'], asset['job_id']
            owned_by_branch = note_id in moving_notes or job_id in moving_jobs
            if not owned_by_branch and asset_id not in inside_assets:
                continue
            shared = (asset_id in outside_assets or (note_id and note_id not in moving_notes)
                      or (job_id in source_jobs and job_id not in moving_jobs))
            if shared:
                attached_note = note_id if note_id in moving_notes and asset['mime'].startswith('image/') else None
                replacements[asset_id] = copy_asset_reference(db, asset, target_id, attached_note, job_id, asset['created_by'])
                if note_id in moving_notes:
                    db.execute('UPDATE assets SET note_id=NULL WHERE id=?', (asset_id,))
            else:
                db.execute('UPDATE assets SET course_id=? WHERE id=?', (target_id, asset_id))

        if replacements:
            for row in notes:
                if row['id'] not in moving_notes:
                    continue
                markdown = replace_note_asset_ids(row['markdown'], replacements)
                if markdown != row['markdown']:
                    db.execute('UPDATE notes SET markdown=?,revision=revision+1 WHERE id=?', (markdown, row['id']))
            bindings = {'notes': json.dumps(list(moving_notes)), 'assets': json.dumps(replacements)}
            versions = db.execute("""SELECT DISTINCT r.note_id,r.revision,r.markdown FROM note_revisions r
                JOIN note_revision_assets ra ON ra.note_id=r.note_id AND ra.revision=r.revision
                WHERE r.note_id IN (SELECT value FROM json_each(:notes))
                  AND ra.asset_id IN (SELECT key FROM json_each(:assets))""", bindings).fetchall()
            db.executemany('UPDATE note_revisions SET markdown=? WHERE note_id=? AND revision=?',
                           ((replace_note_asset_ids(row['markdown'], replacements), row['note_id'], row['revision'])
                            for row in versions))
            db.execute("""UPDATE note_revision_assets AS ra SET asset_id=(
                SELECT value FROM json_each(:assets) WHERE key=ra.asset_id)
                WHERE note_id IN (SELECT value FROM json_each(:notes))
                  AND asset_id IN (SELECT key FROM json_each(:assets))""", bindings)
        return replacements

    def transfer_note(db, note, source, target, target_role, path, user_id):
        note_id, job_id = note['id'], note['job_id']
        source_id, target_id = source['id'], target['id']
        owner = target['owner_id']
        changes_owner = source['owner_id'] != owner
        job = db.execute('SELECT * FROM jobs WHERE id=? AND course_id=? AND note_id=?',
                         (job_id, source_id, note_id)).fetchone()
        payload = json.loads(job['payload']) if job else {}
        if job and (job['status'] != 'ready' or payload.get('videoArchive') in ('queued', 'processing')):
            raise HTTPException(409, 'Дождитесь завершения обработки материалов заметки.')
        if not job:
            job_id = None
        versions = db.execute('SELECT revision,markdown FROM note_revisions WHERE note_id=?', (note_id,)).fetchall()
        asset_ids = note_asset_ids(note['markdown'])
        for version in versions:
            asset_ids.update(note_asset_ids(version['markdown']))
        asset_ids.update(payload[key] for key in job_asset_keys if payload.get(key))
        assets = db.execute("""SELECT * FROM assets WHERE course_id=? AND
            (note_id=? OR job_id=? OR id IN (SELECT value FROM json_each(?)))""",
                            (source_id, note_id, job_id, json.dumps(sorted(asset_ids)))).fetchall()

        before = storage_usage(db, owner)
        editor_percent, editor_before = editor_usage(db, owner) if target_role == 'editor' else (0, 0)
        replacements = {}
        for asset in assets:
            asset_id = asset['id']
            uploader = user_id if changes_owner else asset['created_by']
            if (asset['note_id'] not in (None, note_id) or asset['job_id'] not in (None, job_id)
                    or (source['owner_id'] != user_id and asset['created_by'] != user_id)
                    or asset_used_elsewhere(db, asset_id, source_id, note_id, job_id)):
                # Keep an independent permission reference to the same bytes when the source still uses them.
                replacements[asset_id] = copy_asset_reference(
                    db, asset, target_id, note_id if asset['mime'].startswith('image/') else None,
                    (job_id or asset['job_id']) if asset['job_id'] else None, uploader)
                db.execute('UPDATE assets SET note_id=NULL WHERE id=? AND note_id=?', (asset_id, note_id))
            else:
                db.execute('UPDATE assets SET course_id=?,created_by=? WHERE id=?', (target_id, uploader, asset_id))

        markdown = replace_note_asset_ids(note['markdown'], replacements)
        if changes_owner:
            check_note_quota(db, target_id, markdown)
        db.execute('UPDATE notes SET course_id=?,markdown=?,revision=revision+? WHERE id=?',
                   (target_id, markdown, int(markdown != note['markdown']), note_id))
        for old, new in replacements.items():
            db.execute('UPDATE note_revision_assets SET asset_id=? WHERE note_id=? AND asset_id=?', (new, note_id, old))
        for version in versions:
            updated = replace_note_asset_ids(version['markdown'], replacements)
            if updated != version['markdown']:
                db.execute('UPDATE note_revisions SET markdown=? WHERE note_id=? AND revision=?',
                           (updated, note_id, version['revision']))
        if job:
            payload.update(courseId=target_id, category=path)
            for key in job_asset_keys:
                if payload.get(key) in replacements:
                    payload[key] = replacements[payload[key]]
            db.execute('UPDATE jobs SET course_id=?,payload=? WHERE id=?', (target_id, json.dumps(payload), job_id))

        after = storage_usage(db, owner)
        used_before = before['stored'] + before['reserved']
        used_after = after['stored'] + after['reserved']
        if used_after > max(used_before, settings.user_quota):
            raise HTTPException(507, {'code': 'STORAGE_QUOTA', 'message': 'В категории назначения недостаточно места для вложений.'})
        if target_role == 'editor' and editor_usage(db, owner)[1] > max(editor_before, settings.user_quota * editor_percent // 100):
            raise HTTPException(507, {'code': 'STORAGE_QUOTA', 'message': 'Лимит загрузок редакторов в категории назначения исчерпан.'})
        if changes_owner and db.execute("""SELECT coalesce(sum(r.size),0) FROM note_revisions r
            JOIN notes n ON n.id=r.note_id JOIN courses c ON c.id=n.course_id WHERE c.owner_id=?""",
                                        (owner,)).fetchone()[0] > 25 * 1024 * 1024:
            raise HTTPException(507, 'В категории назначения недостаточно места для истории заметки.')

    @app.put('/api/courses/{course_id}/icon')
    def course_icon(course_id: str, body: dict, user_id=Depends(user)):
        icon = body.get('icon')
        if icon not in ('folder', 'book', 'code', 'science', 'math', 'music'):
            raise HTTPException(422, 'Выберите значок категории.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, course_id, user_id, owner=True)
            path = body.get('path', '')
            if not isinstance(path, str):
                raise HTTPException(422, 'Неверная категория.')
            if path:
                check_folder(db, course_id, path)
                db.execute('UPDATE category_folders SET icon=? WHERE course_id=? AND path=?', (icon, course_id, path))
            else:
                db.execute('INSERT OR REPLACE INTO course_icons VALUES (?,?)', (course_id, icon))
        return {'ok': True}

    @app.get('/api/notes/{note_id}/export')
    def export_note(note_id: str, format: Literal['md', 'zip'] = 'md', user_id=Depends(user)):
        if format == 'zip':
            return export_note_archive(note_id, user_id)
        with connect(settings) as db:
            note = entity(db, 'notes', note_id, user_id)
        quoted = quote(note['title'], safe='')
        return Response(note['markdown'], media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': f"attachment; filename=note.md; filename*=UTF-8''{quoted}.md"})

    def export_note_archive(note_id, user_id):
        entries, names, leases = {}, {}, []
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            note = entity(db, 'notes', note_id, user_id)
            refs = note_asset_ids(note['markdown'])
            job = db.execute('SELECT payload FROM jobs WHERE id=? AND note_id=? AND course_id=?',
                             (note['job_id'], note_id, note['course_id'])).fetchone()
            if job:
                payload = json.loads(job['payload'])
                refs.update(payload[key] for key in ('pdfAssetId', 'transcriptAssetId') if payload.get(key))
            rows = db.execute("""SELECT id,name,path FROM assets WHERE course_id=?
                AND (mime LIKE 'image/%' OR mime IN ('application/pdf','text/plain'))
                AND (note_id=? OR id IN (SELECT value FROM json_each(?))) ORDER BY name,id""",
                              (note['course_id'], note_id, json.dumps(sorted(refs))))
            for row in rows:
                path = Path(row['path'])
                if path not in entries:
                    if not any(regular_upload(path, root) for root in (settings.data, settings.jobs)):
                        continue
                    name = export_filename(row['name'], 'Файл')
                    entries[path] = f'assets/{len(entries) + 1:02d}-{name}'
                    leases.append((uid(), str(path)))
                names[row['id']] = entries[path]
            expiration = now() + 3600_000
            db.executemany('INSERT INTO file_leases VALUES (?,?,?)', ((lease, path, expiration) for lease, path in leases))

        title = export_filename(note['title'], 'Заметка')
        markdown = localized_markdown(note['markdown'], names)

        def download():
            try:
                yield from stream_archive(title, markdown, ((name, path) for path, name in entries.items()))
            finally:
                if leases:
                    with connect(settings) as db:
                        db.executemany('DELETE FROM file_leases WHERE id=?', ((lease,) for lease, _ in leases))

        return StreamingResponse(download(), media_type='application/zip', headers={
            'Content-Disposition': f"attachment; filename=note.zip; filename*=UTF-8''{quote(title, safe='')}.zip",
            'X-Accel-Buffering': 'no',
        })

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
        digest = hashlib.sha256()
        try:
            with path.open('xb') as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, {'field': field, 'message': f'{label}: файл превышает лимит {limit // 1024 // 1024} МБ.'})
                    if not head:
                        head = chunk[:32]
                    digest.update(chunk)
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
            return Upload(asset_id, filename, mime, str(path), size, digest.hexdigest())
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
            await run_in_threadpool(index_matching_legacy_uploads, settings, course_id, item, settings.data / 'assets')
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                current_note = entity(db, 'notes', note_id, user_id, write=True)
                course_id = current_note['course_id']
                check_quota(db, user_id, course_id, uploads=(item,))
                register_upload(db, settings, course_id, note_id, None, item)
                db.execute('UPDATE assets SET created_by=? WHERE id=?', (user_id, item.id))
        except BaseException:
            Path(item.path).unlink(missing_ok=True)
            raise
        return {'url': f'/api/assets/{item.id}', 'id': item.id}

    @app.get('/api/assets/{asset_id}/slides')
    @app.get('/api/assets/{asset_id}/slides/{page}.jpg')
    def slide_preview(asset_id: str, page: int = 0, user_id=Depends(user)):
        from .worker import JobError, rpc
        if not 0 <= page <= 500:
            raise HTTPException(404)
        throttle(settings, 'slide-preview:' + user_id, 180, 60)
        lease = uid()
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = entity(db, 'assets', asset_id, user_id)
            if row['mime'] != 'application/pdf' or not row['job_id']:
                raise HTTPException(404)
            db.execute('INSERT INTO file_leases VALUES (?,?,?)', (lease, row['path'], now() + 120_000))
        try:
            result, _ = media_result(settings, rpc, asset_id, '/slide-preview', {'jobId': row['job_id'], 'page': page}, timeout=105, asset=row)
            with connect(settings) as db:
                entity(db, 'assets', asset_id, user_id)
        except JobError as error:
            raise HTTPException(422, error.message) from None
        finally:
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM file_leases WHERE id=?', (lease,))
                if not db.execute('SELECT 1 FROM assets WHERE path=?', (row['path'],)).fetchone():
                    queue_file_cleanup(db, row['path'])
        if page:
            return Response(base64.b64decode(result['jpeg'], validate=True), media_type='image/jpeg',
                            headers={'Cache-Control': 'private, no-store'})
        return JSONResponse({'pages': result['pages']}, headers={'Cache-Control': 'private, no-store'})

    @app.get('/api/assets/{asset_id}')
    def asset(asset_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = entity(db, 'assets', asset_id, user_id)
            path = Path(row['path'])
            if not any(regular_upload(path, root) for root in (settings.data, settings.jobs)):
                raise HTTPException(404, 'Файл недоступен.')
            try:
                fd = open_asset_fd(path)
            except OSError:
                raise HTTPException(404, 'Файл недоступен.') from None
        return PinnedFileResponse(fd, media_type=row['mime'], filename=row['name'],
                                  content_disposition_type='inline' if row['mime'].startswith('image/') else 'attachment',
                                  headers={'Cache-Control': 'private, no-store'})

    def editor_usage(db, owner_id):
        saved = db.execute('SELECT percent FROM editor_storage_limits WHERE owner_id=?', (owner_id,)).fetchone()
        percent = saved['percent'] if saved else 25
        used = db.execute("""SELECT coalesce(sum(size),0) FROM (
            SELECT max(a.size) AS size FROM assets a JOIN courses c ON c.id=a.course_id
            LEFT JOIN jobs j ON j.id=a.job_id WHERE c.owner_id=?
            AND NOT (a.mime LIKE 'video/%' AND coalesce(json_extract(j.payload,'$.temporaryVideo'),0)=1)
            GROUP BY a.mime,CASE WHEN a.mime LIKE 'video/%' THEN a.path ELSE coalesce(a.sha256,a.path) END
            HAVING max(coalesce(a.created_by,c.owner_id)=c.owner_id)=0)""", (owner_id,)).fetchone()[0]
        reserved = db.execute("""SELECT count(*) * ? FROM assets a JOIN jobs j ON j.id=a.job_id
            JOIN courses c ON c.id=a.course_id WHERE c.owner_id=? AND j.created_by<>c.owner_id
            AND a.mime LIKE 'video/%' AND json_extract(j.payload,'$.temporaryVideo')=1
            AND json_extract(j.payload,'$.transcriptAssetId') IS NULL""", (MAX_TRANSCRIPT_BYTES, owner_id)).fetchone()[0]
        return percent, used + reserved

    def check_quota(db, user_id, course_id=None, temporary=0, uploads=()):
        charged = db.execute('SELECT owner_id FROM courses WHERE id=?', (course_id,)).fetchone()['owner_id'] if course_id else user_id
        usage = storage_usage(db, charged)
        available = max(0, settings.user_quota - usage['stored'] - usage['reserved'])
        reserve = MAX_TRANSCRIPT_BYTES if temporary else 0
        needed = sum(upload_charge(db, settings, item, charged) for item in uploads) + reserve
        if needed > 0 and needed > available:
            free_text, needed_text = (f'{size / 1024**2:.1f}'.replace('.', ',') for size in (available, needed))
            message = (f'В хранилище осталось {free_text} МБ. Для загрузки нужно {needed_text} МБ.'
                       if charged == user_id else 'В хранилище владельца этой категории недостаточно места.')
            raise HTTPException(507, {'code': 'STORAGE_QUOTA', 'message': message})
        if usage['temporary'] + temporary > settings.user_quota:
            raise HTTPException(507, 'Дождитесь расшифровки загруженных видео и попробуйте снова.')
        if temporary:
            sender_temporary = db.execute("""SELECT coalesce(sum(a.size),0) FROM assets a JOIN jobs j ON j.id=a.job_id
                WHERE j.created_by=? AND a.mime LIKE 'video/%' AND json_extract(j.payload,'$.temporaryVideo')=1""", (user_id,)).fetchone()[0]
            if sender_temporary + temporary > settings.user_quota:
                raise HTTPException(507, 'Дождитесь завершения ваших загрузок.')
        if charged != user_id and needed:
            percent, used = editor_usage(db, charged)
            if used + needed > settings.user_quota * percent // 100:
                raise HTTPException(507, {'code': 'STORAGE_QUOTA', 'message': 'Лимит загрузок редакторов в этой категории исчерпан.'})
        total = storage_usage(db)
        physical = sum(upload_charge(db, settings, item) for item in uploads) + reserve + temporary
        if physical and sum(total) + physical > settings.total_quota:
            raise HTTPException(507, 'Хранилище сервиса заполнено. Повторите попытку позже.')

    def storage_items(db, user_id):
        return db.execute("""SELECT a.*,c.title AS course_title,json_extract(j.payload,'$.title') AS job_title,
            json_extract(j.payload,'$.temporaryVideo') AS temporary,
            json_extract(j.payload,'$.videoArchive') AS conversion,
            json_extract(j.payload,'$.videoArchiveError') AS conversion_error,
            coalesce(a.note_id,j.note_id) AS linked_note,j.id AS linked_job
            FROM assets a JOIN courses c ON c.id=a.course_id
            LEFT JOIN jobs j ON j.id=coalesce(a.job_id,(SELECT other.id FROM jobs other WHERE other.course_id=a.course_id
                AND json_extract(other.payload,'$.transcriptAssetId')=a.id ORDER BY other.rowid DESC LIMIT 1))
            WHERE c.owner_id=? ORDER BY a.size DESC,a.id""", (user_id,)).fetchall()

    def storage_key(asset):
        return (asset['mime'], asset['path'] if asset['mime'].startswith('video/') else asset['sha256'] or asset['path'])

    def asset_in_use(db, asset):
        return db.execute("""SELECT 1 FROM jobs WHERE course_id=? AND
            (id=? OR json_extract(payload,'$.transcriptAssetId')=? OR json_extract(payload,'$.pdfAssetId')=?) AND
            (status NOT IN ('ready','failed','cancelled') OR json_extract(payload,'$.videoArchive') IN ('queued','processing'))""",
            (asset['course_id'], asset['job_id'], asset['id'], asset['id'])).fetchone() is not None

    def note_using_asset(db, asset):
        return note_asset_reference(db, asset['course_id'], asset['id'])

    @app.put('/api/storage/editor-limit')
    def set_storage_editor_limit(body: dict, user_id=Depends(user)):
        percent = body.get('percent')
        if type(percent) is not int or not 0 <= percent <= 100:
            raise HTTPException(422, 'Укажите целое число от 0 до 100.')
        with connect(settings) as db:
            db.execute('INSERT OR REPLACE INTO editor_storage_limits VALUES (?,?)', (user_id, percent))
        return {'percent': percent}

    @app.get('/api/storage')
    def storage(user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN')
            usage = storage_usage(db, user_id)
            percent, editors = editor_usage(db, user_id)
            jobs = {}
            active_jobs, active_assets = set(), set()
            for row in db.execute('SELECT j.* FROM jobs j JOIN courses c ON c.id=j.course_id WHERE c.owner_id=?', (user_id,)):
                job = job_json(row)
                ids = {job.get(key) for key in ('transcriptAssetId', 'pdfAssetId', 'sourceAssetId')} - {None}
                for asset_id in ids:
                    jobs.setdefault(asset_id, []).append(dict(id=job['id'], title=job['title'], category=job.get('category', '')))
                if row['status'] not in ('ready', 'failed', 'cancelled') or job.get('videoArchive') in ('queued', 'processing'):
                    active_jobs.add(job['id'])
                    active_assets.update(ids)
            linked_notes = {}
            for note in db.execute('SELECT n.id,n.course_id,n.title,n.markdown FROM notes n JOIN courses c ON c.id=n.course_id WHERE c.owner_id=?', (user_id,)):
                for asset_id in note_asset_ids(note['markdown']):
                    linked_notes.setdefault((note['course_id'], asset_id), {})[note['id']] = dict(id=note['id'], title=note['title'])
            for note in db.execute('''SELECT DISTINCT n.id,n.course_id,n.title,ra.asset_id FROM note_revision_assets ra
                JOIN notes n ON n.id=ra.note_id JOIN courses c ON c.id=n.course_id WHERE c.owner_id=?''', (user_id,)):
                linked_notes.setdefault((note['course_id'], note['asset_id']), {}).setdefault(note['id'],
                    dict(id=note['id'], title=note['title'], historical=True))
            items = []
            groups = {}
            for row in storage_items(db, user_id):
                kind = 'video' if row['mime'].startswith('video/') else 'pdf' if row['mime'] == 'application/pdf' else 'transcript' if row['transcript_language'] else 'image'
                in_use = row['job_id'] in active_jobs or row['id'] in active_assets
                notes = linked_notes.get((row['course_id'], row['id']), {})
                item = dict(id=row['id'], name=row['name'], size=row['size'], kind=kind, courseId=row['course_id'],
                                  courseTitle=row['course_title'], jobId=row['linked_job'], jobTitle=row['job_title'],
                                  noteId=next(iter(notes), row['linked_note']), notes=list(notes.values()), jobs=jobs.get(row['id'], []),
                                  inUse=in_use, referenced=bool(notes), temporary=bool(row['temporary']) and kind == 'video',
                                  conversion=row['conversion'] if kind == 'video' else None,
                                  conversionError=row['conversion_error'] if kind == 'video' else None,
                                  canArchive=kind == 'video' and bool(row['job_id']) and not row['temporary'] and not in_use and not notes)
                items.append(item)
                groups.setdefault(storage_key(row), []).append(row['id'])
        return dict(limit=settings.user_quota, used=usage['stored'], temporary=usage['temporary'],
                    reserved=usage['reserved'], items=items, groups=list(groups.values()), editorPercent=percent, editorUsed=editors)

    @app.post('/api/storage/assets/{asset_id}/archive')
    def archive_storage_video(asset_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            asset = entity(db, 'assets', asset_id, user_id, write=True)
            access(db, asset['course_id'], user_id, owner=True)
            job = db.execute('SELECT * FROM jobs WHERE id=? AND course_id=?', (asset['job_id'], asset['course_id'])).fetchone()
            if not asset['mime'].startswith('video/') or not job or job_json(job).get('sourceAssetId') != asset_id:
                raise HTTPException(409, 'Для этого файла расшифровка недоступна.')
            if asset_in_use(db, asset):
                raise HTTPException(409, 'Дождитесь завершения обработки файла.')
            if note_using_asset(db, asset):
                raise HTTPException(409, 'Видео связано с конспектом или его предыдущей версией.')
            db.execute("""UPDATE jobs SET payload=json_set(json_remove(payload,'$.videoArchiveError'),
                '$.videoArchive','queued') WHERE id=?""", (job['id'],))
        return {'ok': True}

    @app.delete('/api/storage/assets/{asset_id}')
    def delete_storage_asset(asset_id: str, allCopies: bool = False, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            asset = entity(db, 'assets', asset_id, user_id, write=True)
            access(db, asset['course_id'], user_id, owner=True)
            targets = [asset]
            if allCopies:
                key = storage_key(asset)
                targets = [row for row in storage_items(db, user_id) if storage_key(row) == key]
            for row in targets:
                if asset_in_use(db, row):
                    raise HTTPException(409, 'Дождитесь завершения задания.')
                if note_using_asset(db, row):
                    raise HTTPException(409, 'Файл связан с конспектом или его предыдущей версией.')
            for row in targets:
                unlink_storage_asset(db, row)
        return {'ok': True}

    def unlink_storage_asset(db, asset):
        job = db.execute('SELECT * FROM jobs WHERE id=? AND course_id=?', (asset['job_id'], asset['course_id'])).fetchone()
        if job and asset['mime'].startswith('video/') and job_json(job).get('sourceAssetId') == asset['id']:
            discard_job_video(db, settings, job_json(job))
            return
        db.execute("""UPDATE jobs SET payload=json_remove(payload,'$.transcriptAssetId')
            WHERE course_id=? AND json_extract(payload,'$.transcriptAssetId')=?""", (asset['course_id'], asset['id']))
        if asset['mime'] == 'application/pdf':
            db.execute("""UPDATE jobs SET payload=json_remove(payload,'$.pdfAssetId','$.pdfPath','$.pdfName')
                WHERE course_id=? AND json_extract(payload,'$.pdfAssetId')=?""", (asset['course_id'], asset['id']))
        remove_asset(db, settings, asset)

    def check_note_quota(db, course_id, markdown, replacing=None):
        owner = db.execute('SELECT owner_id FROM courses WHERE id=?', (course_id,)).fetchone()['owner_id']
        used = db.execute('SELECT count(*),coalesce(sum(length(CAST(n.markdown AS BLOB))),0) FROM notes n JOIN courses c ON c.id=n.course_id WHERE c.owner_id=? AND n.id!=?', (owner, replacing or '')).fetchone()
        if used[0] >= 5000 or used[1] + len(markdown.encode()) > 25 * 1024 * 1024:
            raise HTTPException(507, 'Лимит заметок владельца категории исчерпан. Удалите ненужные заметки.')

    @app.post('/api/jobs', status_code=201)
    async def create_job(metadata: str = Form(max_length=12000), video: UploadFile | None = File(default=None), pdf: UploadFile | None = File(default=None), user_id=Depends(user)):
        try:
            body = JobInput.model_validate_json(metadata)
            if body.mode == 'mock' and settings.environment != 'test':
                raise HTTPException(422, 'Демонстрационная обработка отключена.')
        except ValueError:
            raise HTTPException(422, 'Проверьте параметры задания.')
        if not body.title.strip():
            raise HTTPException(422, 'Введите название конспекта.')
        with connect(settings) as db:
            access(db, body.courseId, user_id, write=True)
            check_folder(db, body.courseId, body.category)
            count = db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0]
            if count >= 10:
                raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
            check_quota(db, user_id, course_id=body.courseId)
        if body.reuseJobId:
            if body.mode != 'real' or video or pdf or body.sourceType != 'file':
                raise HTTPException(422, 'Выберите один источник для нового конспекта.')
            with connect(settings) as db:
                reusable_materials(db, body, user_id)
        elif body.sourceType == 'url':
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
            selected = next((p for p in status.get('providers', []) if p['id'] == body.provider and p['connected']), None)
            if not selected:
                raise HTTPException(422, {'field': 'provider', 'message': f'Подключите {"API" if body.provider == "openai" else PROVIDERS[body.provider]["name"]} перед созданием конспекта.'})
            if body.provider == 'openai' and body.apiBase and body.apiBase != selected.get('baseUrl'):
                raise HTTPException(409, {'field': 'provider', 'message': 'Адрес подключения изменился. Откройте форму заново и подтвердите адрес.'})
            options = selected.get('models', [])
            if not body.model and options:
                body.model = options[0]['id']
            model = next((m for m in options if m['id'] == body.model), None)
            if not body.model or (options and not model) or (body.provider == 'codex' and not model):
                raise HTTPException(422, {'field': 'provider', 'message': 'Выберите доступную модель.'})
            if model and model.get('efforts') and body.effort not in model['efforts']:
                raise HTTPException(422, {'field': 'provider', 'message': 'Выберите поддерживаемый уровень рассуждения.'})

        job_id = uid()
        directory = settings.jobs / job_id
        assets = []
        try:
            payload = body.model_dump()
            payload.pop('reuseJobId', None)
            if body.mode == 'real':
                payload['effortApplied'] = bool(model and model.get('efforts') and body.provider != 'claude')
                payload['supportsImages'] = bool(model and model.get('supportsImages'))
            if body.mode == 'real' and body.provider == 'openai' and selected.get('baseUrl'):
                payload['apiBase'] = selected['baseUrl']
            payload['timestamps'] = False
            if video:
                item = await store_file(video, directory, settings.max_video, 'video')
                assets.append(item)
                payload.update(sourceName=item.name, sourceSize=item.size, videoPath=Path(item.path).name,
                               sourceAssetId=item.id, sourceSha256=item.sha256, temporaryVideo=True)
            if pdf:
                item = await store_file(pdf, directory, settings.max_pdf, 'pdf')
                assets.append(item)
                payload.update(pdfName=item.name, pdfPath=Path(item.path).name, pdfAssetId=item.id)
            if pdf:
                await run_in_threadpool(index_matching_legacy_uploads, settings, body.courseId, assets[-1], settings.jobs)
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                access(db, body.courseId, user_id, write=True)
                check_folder(db, body.courseId, body.category)
                if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0] >= 10:
                    raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
                if body.reuseJobId:
                    payload.update(reusable_materials(db, body, user_id))
                check_quota(db, user_id, body.courseId, uploads=assets[-1:] if pdf else (),
                            temporary=assets[0].size if payload.get('temporaryVideo') else 0)
                consume_limit(db, 'jobs-day:' + user_id, 30, 86400)
                for item in assets:
                    path = register_upload(db, settings, body.courseId, None, job_id, item)
                    db.execute('UPDATE assets SET created_by=? WHERE id=?', (user_id, item.id))
                    payload['pdfPath' if item.mime == 'application/pdf' else 'videoPath'] = Path(path).name
                db.execute('INSERT INTO jobs(id,course_id,created_by,payload,status,created_at) VALUES (?,?,?,?,?,?)', (job_id, body.courseId, user_id, json.dumps(payload, ensure_ascii=False), 'queued', now()))
            return {'id': job_id}
        except BaseException:
            import shutil
            if directory.exists():
                shutil.rmtree(directory)
            raise

    def reusable_materials(db, body, user_id):
        source = entity(db, 'jobs', body.reuseJobId, user_id, write=True)
        if source['course_id'] != body.courseId:
            raise HTTPException(422, 'Сохраните новый конспект в категории исходных материалов.')
        job = job_json(source)
        transcript_id = job.get('transcriptAssetId')
        transcript = db.execute("SELECT * FROM assets WHERE id=? AND course_id=? AND mime='text/plain'",
                                (transcript_id, body.courseId)).fetchone()
        if not transcript or not regular_upload(Path(transcript['path']), settings.data / 'assets'):
            raise HTTPException(409, 'Расшифровка недоступна. Загрузите исходное видео.')
        materials = dict(transcriptAssetId=transcript_id, sourceName=job.get('sourceName') or transcript['name'],
                         language=job.get('language') or 'ru')
        if job.get('pdfAssetId'):
            pdf = db.execute("SELECT * FROM assets WHERE id=? AND course_id=? AND mime='application/pdf'",
                             (job['pdfAssetId'], body.courseId)).fetchone()
            if not pdf or not regular_upload(Path(pdf['path']), settings.jobs):
                raise HTTPException(409, 'Презентация исходного задания недоступна.')
            materials.update(pdfAssetId=pdf['id'], pdfPath=Path(pdf['path']).name, pdfName=pdf['name'])
        return materials

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            return job_state(entity(db, 'jobs', job_id, user_id))

    @app.post('/api/jobs/{job_id}/{action}')
    def job_action(job_id: str, action: Literal['cancel', 'retry'], user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = entity(db, 'jobs', job_id, user_id, write=True)
            if row['created_by'] != user_id:
                raise HTTPException(403, 'Управлять заданием может его отправитель. Создайте собственное задание для своего аккаунта провайдера.')
            job = job_json(row)
            if action == 'cancel' and row['status'] in ('queued', 'waiting_generation'):
                db.execute("UPDATE jobs SET status='cancelled',completed_at=? WHERE id=?", (now(), job_id))
                if job.get('temporaryVideo'):
                    discard_job_video(db, settings, job)
            elif action == 'retry' and row['status'] == 'failed':
                if job.get('videoArchive') in ('queued', 'processing'):
                    raise HTTPException(409, 'Дождитесь сохранения расшифровки исходного видео.')
                if job.get('mode') != 'mock' and not (job.get('transcriptAssetId') or (job.get('videoPath') and not job.get('temporaryVideo'))):
                    raise HTTPException(409, 'Исходник недоступен. Загрузите видео в новое задание.')
                if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0] >= 10:
                    raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
                db.execute("UPDATE jobs SET payload=json_remove(payload,'$.failedStage','$.stageStartedAt','$.transcriptReused','$.slidesReused','$.slideTextCondensed','$.slidesPartial'),status='queued',created_at=?,started_at=NULL,completed_at=NULL,error=NULL,error_code=NULL,attempt=attempt+1 WHERE id=? AND status='failed'", (now(), job_id))
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
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM imports WHERE user_id=? AND digest=?', (user_id, digest)).fetchone():
                return {'imported': 0}
            if db.execute('SELECT count(*) FROM courses WHERE owner_id=?', (user_id,)).fetchone()[0] + len(courses) > 100:
                raise HTTPException(422, 'Не более 100 собственных категорий.')
            mapping = {}
            for c in courses:
                if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id'] or c['id'] in mapping:
                    raise HTTPException(422, 'Некорректный или повторный идентификатор категории.')
                try:
                    data = CourseInput(title=c.get('title', ''), workspace='personal')
                    if not data.title.strip():
                        raise ValueError()
                except ValueError:
                    raise HTTPException(422, 'Некорректное название импортируемого категории.') from None
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
                    raise HTTPException(422, 'У заметки отсутствует категория.')
                check_note_quota(db, data.courseId, data.markdown)
                timestamp = now()
                db.execute("""INSERT INTO notes(id,course_id,title,markdown,origin,created_at,updated_at,created_by,updated_by)
                            VALUES (?,?,?,?,'manual',?,?,?,?)""",
                           (uid(), data.courseId, data.title, data.markdown, timestamp, timestamp, user_id, user_id))
            db.execute('INSERT INTO imports VALUES (?,?)', (user_id, digest))
        return {'imported': len(notes), 'courses': len(courses)}

    def cleanup(paths, job_ids):
        import shutil
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            for raw in paths:
                queue_file_cleanup(db, raw)
            for job_id in job_ids:
                if not re.fullmatch(r'[a-f0-9-]{36}', job_id):
                    continue
                path = settings.jobs / job_id
                prefix = str(path) + '/'
                if db.execute('SELECT 1 FROM assets WHERE instr(path,?)=1', (prefix,)).fetchone():
                    continue
                if db.execute('SELECT 1 FROM file_leases WHERE instr(path,?)=1 AND expires>?', (prefix, now())).fetchone():
                    continue
                if path.is_symlink():
                    path.unlink()
                elif path.exists():
                    shutil.rmtree(path)

    def delete_course_rows(db, course_id):
        active = db.execute("""SELECT 1 FROM jobs WHERE course_id=? AND (status IN ('acquiring','transcribing','preparing','generating')
            OR json_extract(payload,'$.videoArchive')='processing')""", (course_id,)).fetchone()
        if active:
            raise HTTPException(409, 'Дождитесь завершения активного задания перед удалением.')
        paths = [row['path'] for row in db.execute('SELECT path FROM assets WHERE course_id=?', (course_id,))]
        job_ids = [row['id'] for row in db.execute('SELECT id FROM jobs WHERE course_id=?', (course_id,))]
        for table in ['notes', 'assets', 'jobs', 'members']:
            db.execute(f'DELETE FROM {table} WHERE course_id=?', (course_id,))
        db.execute('DELETE FROM courses WHERE id=?', (course_id,))
        for path in paths:
            queue_file_cleanup(db, path)
        return [], job_ids

    @app.delete('/api/courses/{course_id}')
    def delete_course(course_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            access(db, course_id, user_id, owner=True)
            paths, job_ids = delete_course_rows(db, course_id)
        cleanup(paths, job_ids)
        return {'ok': True}

    @app.delete('/api/notes/{note_id}')
    def delete_note(note_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            note, _, role = note_for_edit(db, note_id, user_id)
            if role != 'owner' and note['created_by'] != user_id:
                raise HTTPException(403, 'Удалить заметку может её автор или владелец категории.')
            db.execute('DELETE FROM notes WHERE id=?', (note_id,))
            for asset in db.execute('SELECT * FROM assets WHERE note_id=?', (note_id,)).fetchall():
                if (role != 'owner' and asset['created_by'] != user_id) or note_asset_reference(db, asset['course_id'], asset['id']):
                    db.execute('UPDATE assets SET note_id=NULL WHERE id=?', (asset['id'],))
                else:
                    db.execute('DELETE FROM assets WHERE id=?', (asset['id'],))
                    queue_file_cleanup(db, asset['path'])
            db.execute('UPDATE jobs SET note_id=NULL WHERE note_id=?', (note_id,))
        return {'ok': True}

    @app.delete('/api/account')
    @account_action
    def delete_account(body: PasswordInput, response: Response, user_id=Depends(user)):
        from .worker import JobError, rpc
        throttle(settings, 'delete-account:' + user_id, 5, 3600)
        with connect(settings) as db:
            account = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not account or account['disabled']:
            raise HTTPException(409, 'Выполняется удаление аккаунта.')
        if not password_matches(account['password_hash'], body.password):
            raise HTTPException(401, 'Неверный пароль.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("""SELECT 1 FROM jobs WHERE (created_by=? OR course_id IN (SELECT id FROM courses WHERE owner_id=?))
                AND (status IN ('acquiring','transcribing','preparing','generating') OR json_extract(payload,'$.videoArchive')='processing')""",
                (user_id, user_id)).fetchone():
                raise HTTPException(409, 'Дождитесь завершения активных заданий перед удалением аккаунта.')
            result = db.execute('UPDATE users SET disabled=2 WHERE id=? AND password_hash=?', (user_id, account['password_hash']))
            if result.rowcount != 1:
                raise HTTPException(409, 'Аккаунт изменён. Повторите вход.')
            has_profile = bool(db.execute('SELECT 1 FROM provider_links WHERE user_id=?', (user_id,)).fetchone())
        if has_profile:
            try:
                rpc(settings, settings.providers_url, '/profiles/purge', {'userId': user_id}, timeout=40)
            except JobError:
                with connect(settings) as db:
                    db.execute('UPDATE users SET disabled=0 WHERE id=?', (user_id,))
                raise HTTPException(503, 'Не удалось удалить подключение провайдера. Аккаунт сохранён; повторите попытку позже.') from None
        paths, jobs = [], []
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            cancelled = db.execute("""SELECT * FROM jobs WHERE status='queued' AND created_by=? AND
                course_id NOT IN (SELECT id FROM courses WHERE owner_id=?)
                AND json_extract(payload,'$.temporaryVideo')=1""", (user_id, user_id)).fetchall()
            db.execute("UPDATE jobs SET status='cancelled',completed_at=? WHERE status IN ('queued','waiting_generation') AND (created_by=? OR course_id IN (SELECT id FROM courses WHERE owner_id=?))", (now(), user_id, user_id))
            for row in cancelled:
                discard_job_video(db, settings, job_json(row))
            for course in db.execute('SELECT id FROM courses WHERE owner_id=?', (user_id,)).fetchall():
                file_paths, job_ids = delete_course_rows(db, course['id'])
                paths.extend(file_paths); jobs.extend(job_ids)
            for table in ['members', 'sessions', 'auth_tokens', 'imports', 'provider_links']:
                db.execute(f'DELETE FROM {table} WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM mail_outbox WHERE recipient=?', (account['email'],))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))
        cleanup(paths, jobs)
        response.delete_cookie(settings.cookie_name, path='/', secure=settings.production, httponly=True, samesite='strict')
        return {'ok': True}

    return app


app = create_app()
