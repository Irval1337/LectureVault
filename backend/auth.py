"""Application accounts are independent from optional AI provider accounts."""
import hashlib
import ipaddress
import secrets
import sqlite3
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .db import connect, now, uid
from .mailer import enqueue

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
HASH_SLOTS = threading.BoundedSemaphore(4)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
NOTICE = 'Если аккаунт с таким email существует, письмо будет отправлено. Проверьте входящие и спам.'


def email_address(value):
    try:
        return validate_email(value, check_deliverability=False, allow_smtputf8=False).normalized.casefold()
    except (EmailNotValidError, TypeError):
        raise HTTPException(422, {'field': 'email', 'message': 'Введите корректный email.'}) from None


def hash_password(value):
    with HASH_SLOTS:
        return PASSWORDS.hash(value)


def password_matches(encoded, value):
    try:
        with HASH_SLOTS:
            return PASSWORDS.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


def public_user(row):
    return {'id': row['id'], 'name': row['name'], 'email': row['email'], 'verified': bool(row['verified'])}


def throttle(settings, key, limit, seconds):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        consume_limit(db, key, limit, seconds)


def consume_limit(db, key, limit, seconds):
    digest = hashlib.sha256(key.encode()).hexdigest()
    window = now() // (seconds * 1000)
    row = db.execute('SELECT * FROM rate_limits WHERE key=?', (digest,)).fetchone()
    if row and row['window'] == window and row['count'] >= limit:
        raise HTTPException(429, 'Слишком много попыток. Попробуйте позже.', headers={'Retry-After': str(seconds)})
    db.execute('DELETE FROM rate_limits WHERE expires<?', (now(),))
    db.execute('INSERT INTO rate_limits VALUES (?,?,1,?) ON CONFLICT(key) DO UPDATE SET window=excluded.window,expires=excluded.expires,count=CASE WHEN window=excluded.window THEN count+1 ELSE 1 END', (digest, window, (window + 1) * seconds * 1000))


def client_key(request):
    # The API is not published. Nginx overwrites this header at the trusted ingress.
    value = request.headers.get('x-real-ip', request.client.host if request.client else 'unknown')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return 'local'


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    password: str = Field(min_length=15, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class EmailInput(BaseModel):
    email: str = Field(max_length=254)


class TokenInput(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class ResetInput(TokenInput):
    password: str = Field(min_length=15, max_length=128)


class PasswordInput(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(PasswordInput):
    newPassword: str = Field(min_length=15, max_length=128)


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def install_auth(app, settings):
    router = APIRouter(prefix='/api/auth')

    def current_user(request: Request):
        token = request.cookies.get(settings.cookie_name, '')
        if not token or len(token) > 128:
            raise HTTPException(401, 'Войдите в аккаунт.')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with connect(settings) as db:
            row = db.execute('SELECT s.*,u.disabled FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.digest=? AND s.expires>? AND s.last_seen>? AND u.disabled!=1', (digest, now(), now() - 2 * 3600_000)).fetchone()
            if not row:
                raise HTTPException(401, 'Сессия истекла. Войдите снова.')
            if row['disabled'] == 2:
                raise HTTPException(409, 'Выполняется удаление аккаунта. Дождитесь завершения.')
            if row['last_seen'] < now() - 60_000:
                db.execute('UPDATE sessions SET last_seen=? WHERE digest=?', (now(), digest))
        request.state.session = dict(row)
        return row['user_id']

    def recent_user(request: Request, user_id=Depends(current_user)):
        if request.state.session['created_at'] < now() - 15 * 60_000:
            raise HTTPException(428, 'Подтвердите пароль для этого действия.')
        return user_id

    def set_session(db, response, user_id, old_token=''):
        if old_token:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(old_token.encode()).hexdigest(),))
        db.execute('DELETE FROM sessions WHERE expires<? OR last_seen<?', (now(), now() - 2 * 3600_000))
        db.execute('DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 19)', (user_id,))
        token = secrets.token_urlsafe(40)
        db.execute('INSERT INTO sessions(digest,user_id,expires,created_at,last_seen) VALUES (?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user_id, now() + 8 * 3600_000, now(), now()))
        response.set_cookie(settings.cookie_name, token, httponly=True, secure=settings.production, samesite='strict', max_age=8 * 3600, path='/')

    def issue_token(db, row, purpose):
        token = secrets.token_urlsafe(40)
        db.execute('DELETE FROM auth_tokens WHERE user_id=? AND purpose=?', (row['id'], purpose))
        db.execute('DELETE FROM auth_tokens WHERE expires<?', (now(),))
        duration = 24 * 3600_000 if purpose == 'verify' else 30 * 60_000
        db.execute('INSERT INTO auth_tokens VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], purpose, now() + duration))
        enqueue(settings, db, row['email'], purpose, token)

    def consume_token(db, token, purpose):
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = db.execute('SELECT t.*,u.email,u.disabled FROM auth_tokens t JOIN users u ON u.id=t.user_id WHERE t.digest=? AND t.purpose=? AND t.expires>? AND u.disabled=0', (digest, purpose, now())).fetchone()
        if not row:
            raise HTTPException(410, 'Ссылка недействительна или истекла. Запросите новую.')
        db.execute('DELETE FROM auth_tokens WHERE digest=?', (digest,))
        return row

    @router.get('')
    def auth_info(request: Request):
        result = None
        try:
            user_id = current_user(request)
            with connect(settings) as db:
                result = public_user(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
        except HTTPException:
            pass
        return {'user': result, 'emailVerification': settings.verify_email, 'supportEmail': settings.support_email}

    @router.post('/register')
    def register(body: Registration, request: Request, response: Response):
        throttle(settings, 'register:' + client_key(request), 5, 3600)
        email = email_address(body.email)
        name = body.name.strip()
        if not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise HTTPException(422, {'field': 'name', 'message': 'Введите имя без управляющих символов.'})
        encoded = hash_password(body.password)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
                return {'ok': True, 'message': 'Если регистрация возможна, письмо будет отправлено. Если аккаунт уже есть, используйте вход или восстановление пароля.'}
            user_id = uid()
            db.execute('INSERT INTO users(id,email,name,password_hash,verified,created_at) VALUES (?,?,?,?,?,?)', (user_id, email, name, encoded, int(not settings.verify_email), now()))
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if settings.verify_email:
                issue_token(db, row, 'verify')
            else:
                set_session(db, response, user_id)
        return {'ok': True, 'message': 'Если регистрация возможна, письмо будет отправлено. Если аккаунт уже есть, используйте вход или восстановление пароля.'}

    @router.post('/login')
    def login(body: Credentials, request: Request, response: Response):
        throttle(settings, 'login-ip:' + client_key(request), 30, 900)
        email = email_address(body.email)
        throttle(settings, 'login-email:' + email, 15, 900)
        with connect(settings) as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        valid = password_matches(row['password_hash'] if row else DUMMY_HASH, body.password)
        if not valid or not row or row['disabled']:
            raise HTTPException(401, 'Неверный email или пароль.')
        if settings.verify_email and not row['verified']:
            raise HTTPException(403, 'Подтвердите email по ссылке из письма.')
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            # Recheck after the expensive hash in case a password reset raced login.
            current = db.execute('SELECT * FROM users WHERE id=? AND password_hash=? AND disabled=0', (row['id'], row['password_hash'])).fetchone()
            if not current:
                raise HTTPException(401, 'Неверный email или пароль.')
            if PASSWORDS.check_needs_rehash(current['password_hash']):
                db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(body.password), row['id']))
            set_session(db, response, row['id'], request.cookies.get(settings.cookie_name, ''))
        return {'user': public_user(row)}

    @router.post('/verify-email')
    def verify(body: TokenInput, request: Request, response: Response):
        throttle(settings, 'verify:' + client_key(request), 20, 900)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            token = consume_token(db, body.token, 'verify')
            db.execute('UPDATE users SET verified=1 WHERE id=?', (token['user_id'],))
            set_session(db, response, token['user_id'], request.cookies.get(settings.cookie_name, ''))
            row = db.execute('SELECT * FROM users WHERE id=?', (token['user_id'],)).fetchone()
        return {'user': public_user(row)}

    @router.post('/resend-verification')
    @router.post('/forgot-password')
    def send_link(body: EmailInput, request: Request):
        email = email_address(body.email)
        purpose = 'reset' if request.url.path.endswith('forgot-password') else 'verify'
        throttle(settings, purpose + '-ip:' + client_key(request), 10, 3600)
        throttle(settings, purpose + '-email:' + email, 3, 3600)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE email=? AND disabled=0', (email,)).fetchone()
            if row and (purpose == 'reset' or not row['verified']):
                issue_token(db, row, purpose)
        return {'ok': True, 'message': NOTICE}

    @router.post('/reset-password')
    def reset_password(body: ResetInput, request: Request):
        throttle(settings, 'reset-token:' + client_key(request), 15, 900)
        encoded = hash_password(body.password)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            token = consume_token(db, body.token, 'reset')
            db.execute('UPDATE users SET password_hash=?,verified=1 WHERE id=?', (encoded, token['user_id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (token['user_id'],))
            db.execute('DELETE FROM auth_tokens WHERE user_id=?', (token['user_id'],))
            enqueue(settings, db, token['email'], 'password-changed')
        return {'ok': True}

    @router.post('/logout')
    def logout(request: Request, response: Response):
        token = request.cookies.get(settings.cookie_name, '')
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie(settings.cookie_name, path='/', secure=settings.production, httponly=True, samesite='strict')
        return {'ok': True}

    @router.post('/logout-all')
    def logout_all(response: Response, user_id=Depends(current_user)):
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        response.delete_cookie(settings.cookie_name, path='/', secure=settings.production, httponly=True, samesite='strict')
        return {'ok': True}

    @router.post('/reauth')
    def reauth(body: PasswordInput, request: Request, user_id=Depends(current_user)):
        throttle(settings, 'reauth:' + user_id, 10, 900)
        with connect(settings) as db:
            row = db.execute('SELECT password_hash FROM users WHERE id=?', (user_id,)).fetchone()
        if not password_matches(row['password_hash'], body.password):
            raise HTTPException(401, 'Неверный пароль.')
        with connect(settings) as db:
            db.execute('UPDATE sessions SET created_at=?,last_seen=? WHERE digest=?', (now(), now(), request.state.session['digest']))
        return {'ok': True}

    @router.patch('/profile')
    def profile(body: ProfileInput, user_id=Depends(current_user)):
        name = body.name.strip()
        if not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise HTTPException(422, 'Введите имя без управляющих символов.')
        with connect(settings) as db:
            db.execute('UPDATE users SET name=? WHERE id=?', (name, user_id))
            return {'user': public_user(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())}

    @router.post('/change-password')
    def change_password(body: PasswordChange, request: Request, response: Response, user_id=Depends(current_user)):
        throttle(settings, 'password-change:' + user_id, 10, 3600)
        with connect(settings) as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not password_matches(row['password_hash'], body.password):
            raise HTTPException(401, 'Неверный текущий пароль.')
        encoded = hash_password(body.newPassword)
        with connect(settings) as db:
            db.execute('BEGIN IMMEDIATE')
            updated = db.execute('UPDATE users SET password_hash=? WHERE id=? AND password_hash=?', (encoded, user_id, row['password_hash']))
            if updated.rowcount != 1:
                raise HTTPException(409, 'Пароль уже изменён. Войдите снова.')
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM auth_tokens WHERE user_id=?', (user_id,))
            set_session(db, response, user_id)
            enqueue(settings, db, row['email'], 'password-changed')
        return {'ok': True}

    app.include_router(router)
    return current_user, recent_user
