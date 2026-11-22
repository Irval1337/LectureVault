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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError

from .config import Settings
from .db import connect, course_json, initialize, job_json, note_json, now, uid
from .media import Upload, index_matching_legacy_uploads, register_upload, media_result
from .providers import CATALOG, PROVIDERS, ProviderId, valid_api_key, valid_options
from .auth import install_auth, public_user, email_address, throttle, consume_limit, PasswordInput, password_matches


class CourseInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    workspace: Literal['personal', 'shared'] = 'personal'


class NoteInput(BaseModel):
    category: str = Field(default="", max_length=250)
    courseId: str
    title: str = Field(default='Без названия', min_length=1, max_length=120)
    markdown: str = Field(default='', max_length=200_000)


class NoteEdit(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    markdown: str = Field(max_length=200_000)
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
    effort: Literal['low', 'medium', 'high', 'xhigh'] = 'medium'

    @model_validator(mode='after')
    def options(self):
        if self.model and not valid_options(self.provider, self.model, self.effort):
            raise ValueError('Unsupported model or effort')
        return self


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
        result.pop('apiBase', None)
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
            notes = [dict(id=row['id'], courseId=row['course_id'], category=row['path'] or '', title=row['title'], markdown='', contentLoaded=False, origin=row['origin'], revision=row['revision'], createdAt=row['created_at'], updatedAt=row['updated_at'], jobId=row['job_id'], isMock=bool(row['is_mock'])) for row in db.execute(f'SELECT id,course_id,title,origin,revision,created_at,updated_at,job_id,is_mock,path FROM notes LEFT JOIN note_categories ON note_categories.note_id=notes.id WHERE course_id IN ({marks}) ORDER BY created_at', ids)]
            jobs = [job_state(row) for row in db.execute(f'SELECT * FROM jobs WHERE course_id IN ({marks}) ORDER BY created_at', ids)]
        return {'version': 1, 'courses': courses, 'notes': notes, 'jobs': jobs}

    @app.get('/api/providers')
    def providers(user_id=Depends(user)):
        throttle(settings, 'providers-status:' + user_id, 90, 60)
        from .worker import JobError, rpc
        try:
            return rpc(settings, settings.provider_url(user_id), '/providers/status', {'userId': user_id}, timeout=35)
        except JobError:
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
            if db.execute('SELECT count(*) FROM courses WHERE owner_id=?', (user_id,)).fetchone()[0] >= 100:
                raise HTTPException(422, 'Не более 100 собственных категорий.')
            titles = db.execute('SELECT title FROM courses WHERE owner_id=?', (user_id,))
            if any(row['title'].casefold() == title.casefold() for row in titles):
                raise HTTPException(409, 'Категория с таким названием уже существует.')
            course_id = uid()
            db.execute('INSERT INTO courses VALUES (?,?,?,?,?)', (course_id, title, body.workspace, user_id, now()))
        return {'id': course_id}

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
                db.execute("""UPDATE jobs SET
                    completed_at=CASE WHEN status='queued' THEN ? ELSE completed_at END,
                    status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
                    error_code='ACCESS_REVOKED',error='Права на запись в категорию отозваны.'
                    WHERE course_id=? AND created_by=? AND status NOT IN ('failed','ready','cancelled')""",
                    (now(), course_id, member_id))
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
            note_id = uid()
            db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?)', (note_id, body.courseId, body.title.strip(), body.markdown, 'manual', 1, now(), now(), None, 0))
            if body.category:
                db.execute('INSERT INTO note_categories VALUES (?,?)', (note_id, body.category))
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
            db.execute('BEGIN IMMEDIATE')
            row = entity(db, 'notes', note_id, user_id, write=True)
            check_note_quota(db, row['course_id'], body.markdown, replacing=note_id)
            result = db.execute('UPDATE notes SET title=?,markdown=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?', (body.title.strip(), body.markdown, now(), note_id, body.revision))
            if result.rowcount != 1:
                raise HTTPException(409, 'Документ уже изменён. Черновик сохранён; откройте актуальную версию перед повторным сохранением.')
            return note_json(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())

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

    @app.put('/api/notes/{note_id}/category')
    def move_note(note_id: str, body: dict, user_id=Depends(user)):
        parts = folder_parts(body.get('path'))
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            note = entity(db, 'notes', note_id, user_id, write=True)
            if body.get('existingOnly'):
                check_folder(db, note['course_id'], '/'.join(parts))
            else:
                ensure_folders(db, note['course_id'], parts)
            db.execute('DELETE FROM note_categories WHERE note_id=?', (note_id,))
            if parts:
                db.execute('INSERT INTO note_categories VALUES (?,?)', (note_id, '/'.join(parts)))
        return {'ok': True}

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
                entity(db, 'notes', note_id, user_id, write=True)
                check_quota(db, user_id, item.size, course_id)
                register_upload(db, settings, course_id, note_id, None, item)
        except BaseException:
            Path(item.path).unlink(missing_ok=True)
            raise
        return {'url': f'/api/assets/{item.id}', 'id': item.id}

    @app.get('/api/assets/{asset_id}/slides')
    @app.get('/api/assets/{asset_id}/slides/{page}.jpg')
    def slide_preview(asset_id: str, page: int = 0, user_id=Depends(user)):
        from .worker import JobError, rpc
        with connect(settings) as db:
            row = entity(db, 'assets', asset_id, user_id)
        if row['mime'] != 'application/pdf' or not row['job_id'] or not 0 <= page <= 500:
            raise HTTPException(404)
        throttle(settings, 'slide-preview:' + user_id, 180, 60)
        try:
            result, _ = media_result(settings, rpc, asset_id, '/slide-preview', {'jobId': row['job_id'], 'page': page}, timeout=45)
        except JobError as error:
            raise HTTPException(422, error.message) from None
        with connect(settings) as db:
            entity(db, 'assets', asset_id, user_id)
        if page:
            return Response(base64.b64decode(result['jpeg'], validate=True), media_type='image/jpeg')
        return {'pages': result['pages']}

    @app.get('/api/assets/{asset_id}')
    def asset(asset_id: str, user_id=Depends(user)):
        with connect(settings) as db:
            row = entity(db, 'assets', asset_id, user_id)
        original = Path(row['path'])
        path = original.resolve()
        if any(p.is_symlink() for p in [original, *original.parents]) or not (path.is_relative_to(settings.data.resolve()) or path.is_relative_to(settings.jobs.resolve())) or not path.is_file():
            raise HTTPException(404, 'Файл недоступен.')
        return FileResponse(path, media_type=row['mime'], filename=row['name'], content_disposition_type='inline' if row['mime'].startswith('image/') else 'attachment')

    def check_quota(db, user_id, added=0, course_id=None):
        charged = db.execute('SELECT owner_id FROM courses WHERE id=?', (course_id,)).fetchone()['owner_id'] if course_id else user_id
        used = db.execute('SELECT coalesce(sum(a.size),0) FROM assets a JOIN courses c ON c.id=a.course_id WHERE c.owner_id=?', (charged,)).fetchone()[0]
        if used + added > settings.user_quota:
            raise HTTPException(507, 'Лимит хранилища владельца категории исчерпан. Удалите ненужные материалы.')
        if db.execute('SELECT coalesce(sum(size),0) FROM assets').fetchone()[0] + added > settings.total_quota:
            raise HTTPException(507, 'Хранилище сервиса заполнено. Повторите попытку позже.')

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
            selected = next((p for p in status.get('providers', []) if p['id'] == body.provider and p['connected']), None)
            if not selected:
                raise HTTPException(422, {'field': 'provider', 'message': f'Подключите {"API" if body.provider == "openai" else PROVIDERS[body.provider]["name"]} перед отправкой видео.'})
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
            if body.mode == 'real':
                payload['effortApplied'] = bool(model and model.get('efforts') and body.provider != 'claude')
            if body.mode == 'real' and body.provider == 'openai' and selected.get('baseUrl'):
                payload['apiBase'] = selected['baseUrl']
            payload['timestamps'] = False
            if video:
                item = await store_file(video, directory, settings.max_video, 'video')
                assets.append(item)
                payload.update(sourceName=item.name, sourceSize=item.size, videoPath=Path(item.path).name, sourceAssetId=item.id)
            if pdf:
                item = await store_file(pdf, directory, settings.max_pdf, 'pdf')
                assets.append(item)
                payload.update(pdfName=item.name, pdfPath=Path(item.path).name, pdfAssetId=item.id)
            for item in assets:
                await run_in_threadpool(index_matching_legacy_uploads, settings, body.courseId, item, settings.jobs)
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                access(db, body.courseId, user_id, write=True)
                check_folder(db, body.courseId, body.category)
                if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0] >= 10:
                    raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
                check_quota(db, user_id, sum(item.size for item in assets), body.courseId)
                consume_limit(db, 'jobs-day:' + user_id, 30, 86400)
                for item in assets:
                    path = register_upload(db, settings, body.courseId, None, job_id, item)
                    payload['pdfPath' if item.mime == 'application/pdf' else 'videoPath'] = Path(path).name
                db.execute('INSERT INTO jobs(id,course_id,created_by,payload,status,created_at) VALUES (?,?,?,?,?,?)', (job_id, body.courseId, user_id, json.dumps(payload, ensure_ascii=False), 'queued', now()))
            return {'id': job_id}
        except BaseException:
            import shutil
            if directory.exists():
                shutil.rmtree(directory)
            raise

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
            if action == 'cancel' and row['status'] == 'queued':
                db.execute("UPDATE jobs SET status='cancelled',completed_at=? WHERE id=? AND status='queued'", (now(), job_id))
            elif action == 'retry' and row['status'] == 'failed':
                if db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('ready','failed','cancelled') AND created_by=?", (user_id,)).fetchone()[0] >= 10:
                    raise HTTPException(429, 'Не более 10 активных заданий на пользователя.')
                db.execute("UPDATE jobs SET payload=json_remove(payload,'$.failedStage','$.stageStartedAt','$.transcriptReused','$.slidesReused','$.slideTextCondensed'),status='queued',created_at=?,started_at=NULL,completed_at=NULL,error=NULL,error_code=NULL,attempt=attempt+1 WHERE id=? AND status='failed'", (now(), job_id))
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
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?)', (uid(), data.courseId, data.title, data.markdown, 'manual', 1, now(), now(), None, 0))
            db.execute('INSERT INTO imports VALUES (?,?)', (user_id, digest))
        return {'imported': len(notes), 'courses': len(courses)}

    def cleanup(paths, job_ids):
        import shutil
        for raw in paths:
            path = Path(raw)
            if path.resolve().is_relative_to(settings.data.resolve()) or path.resolve().is_relative_to(settings.jobs.resolve()):
                path.unlink(missing_ok=True)
        for job_id in job_ids:
            if re.fullmatch(r'[a-f0-9-]{36}', job_id):
                path = settings.jobs / job_id
                if path.is_symlink():
                    path.unlink()
                elif path.exists():
                    shutil.rmtree(path)

    def delete_course_rows(db, course_id):
        active = db.execute("SELECT 1 FROM jobs WHERE course_id=? AND status IN ('acquiring','transcribing','preparing','generating')", (course_id,)).fetchone()
        if active:
            raise HTTPException(409, 'Дождитесь завершения активного задания перед удалением.')
        paths = [row['path'] for row in db.execute('SELECT path FROM assets WHERE course_id=?', (course_id,))]
        job_ids = [row['id'] for row in db.execute('SELECT id FROM jobs WHERE course_id=?', (course_id,))]
        for table in ['notes', 'assets', 'jobs', 'members']:
            db.execute(f'DELETE FROM {table} WHERE course_id=?', (course_id,))
        db.execute('DELETE FROM courses WHERE id=?', (course_id,))
        return paths, job_ids

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
        paths = []
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            entity(db, 'notes', note_id, user_id, write=True)
            for asset in db.execute('SELECT * FROM assets WHERE note_id=?', (note_id,)).fetchall():
                referenced = db.execute('SELECT 1 FROM notes WHERE id!=? AND course_id=? AND instr(markdown,?)>0', (note_id, asset['course_id'], '/api/assets/' + asset['id'])).fetchone()
                if referenced:
                    db.execute('UPDATE assets SET note_id=NULL WHERE id=?', (asset['id'],))
                else:
                    db.execute('DELETE FROM assets WHERE id=?', (asset['id'],))
                    if not db.execute('SELECT 1 FROM assets WHERE path=?', (asset['path'],)).fetchone():
                        paths.append(asset['path'])
            db.execute('DELETE FROM notes WHERE id=?', (note_id,))
            db.execute('UPDATE jobs SET note_id=NULL WHERE note_id=?', (note_id,))
        cleanup(paths, [])
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
            if db.execute("SELECT 1 FROM jobs WHERE (created_by=? OR course_id IN (SELECT id FROM courses WHERE owner_id=?)) AND status IN ('acquiring','transcribing','preparing','generating')", (user_id, user_id)).fetchone():
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
            db.execute("UPDATE jobs SET status='cancelled',completed_at=? WHERE status='queued' AND (created_by=? OR course_id IN (SELECT id FROM courses WHERE owner_id=?))", (now(), user_id, user_id))
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
