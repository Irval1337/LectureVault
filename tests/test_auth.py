import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.config import Settings
from backend.db import connect, now
from backend.mailer import cipher, deliver_one
from tests.test_api import HEADERS, PASSWORD, settings_at


def token_for(settings, purpose):
    with connect(settings) as db:
        rows = db.execute('SELECT * FROM mail_outbox ORDER BY created_at DESC,rowid DESC').fetchall()
    return next(json.loads(cipher(settings).decrypt(row['payload'].encode()))['token'] for row in rows if row['payload'] and json.loads(cipher(settings).decrypt(row['payload'].encode()))['purpose'] == purpose)


def register_pending(c):
    response = c.post('/api/auth/register', json={'email': 'person@example.com', 'name': 'Человек', 'password': PASSWORD})
    assert response.status_code == 200
    assert c.get('/api/auth').json()['user'] is None


def test_email_verification_is_single_use_and_not_exposed_in_api(tmp_path):
    settings = settings_at(tmp_path); settings.verify_email = True
    with TestClient(create_app(settings), headers=HEADERS) as c:
        register_pending(c)
        assert c.post('/api/auth/login', json={'email': 'person@example.com', 'password': PASSWORD}).status_code == 403
        token = token_for(settings, 'verify')
        with connect(settings) as db:
            stored = db.execute('SELECT digest FROM auth_tokens').fetchone()[0]
            assert stored == hashlib.sha256(token.encode()).hexdigest()
            assert token not in db.execute('SELECT payload FROM mail_outbox').fetchone()[0]
        assert token not in c.get('/api/auth').text
        response = c.post('/api/auth/verify-email', json={'token': token})
        assert response.status_code == 200 and response.json()['user']['verified']
        assert c.get('/api/state').json()['courses'] == []
        assert c.post('/api/auth/verify-email', json={'token': token}).status_code == 410


def test_verification_resend_invalidates_old_link_and_expired_links_fail(tmp_path):
    settings = settings_at(tmp_path); settings.verify_email = True
    with TestClient(create_app(settings), headers=HEADERS) as c:
        register_pending(c); old = token_for(settings, 'verify')
        assert c.post('/api/auth/resend-verification', json={'email': 'person@example.com'}).status_code == 200
        fresh = token_for(settings, 'verify'); assert fresh != old
        assert c.post('/api/auth/verify-email', json={'token': old}).status_code == 410
        with connect(settings) as db: db.execute('UPDATE auth_tokens SET expires=?', (now() - 1,))
        assert c.post('/api/auth/verify-email', json={'token': fresh}).status_code == 410


def test_reset_password_generic_response_revokes_sessions_and_requires_login(tmp_path):
    settings = settings_at(tmp_path); settings.verify_email = True
    with TestClient(create_app(settings), headers=HEADERS) as c:
        register_pending(c); c.post('/api/auth/verify-email', json={'token': token_for(settings, 'verify')})
        known = c.post('/api/auth/forgot-password', json={'email': 'person@example.com'})
        unknown = c.post('/api/auth/forgot-password', json={'email': 'unknown@example.com'})
        assert known.json() == unknown.json()
        token = token_for(settings, 'reset')
        assert c.post('/api/auth/verify-email', json={'token': token}).status_code == 410
        new_password = 'Another long phrase for the new password'
        assert c.post('/api/auth/reset-password', json={'token': token, 'password': new_password}).status_code == 200
        assert c.get('/api/state').status_code == 401
        assert c.post('/api/auth/reset-password', json={'token': token, 'password': PASSWORD}).status_code == 410
        assert c.post('/api/auth/login', json={'email': 'person@example.com', 'password': PASSWORD}).status_code == 401
        assert c.post('/api/auth/login', json={'email': 'person@example.com', 'password': new_password}).status_code == 200


def test_change_password_revokes_other_sessions_and_all_logout(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings), headers=HEADERS) as a, TestClient(create_app(settings), headers=HEADERS) as b:
        a.post('/api/auth/register', json={'email': 'person@example.com', 'name': 'User', 'password': PASSWORD})
        b.post('/api/auth/login', json={'email': 'person@example.com', 'password': PASSWORD})
        assert a.post('/api/auth/change-password', json={'password': 'wrong', 'newPassword': 'A different new long password'}).status_code == 401
        assert a.post('/api/auth/change-password', json={'password': PASSWORD, 'newPassword': 'A different new long password'}).status_code == 200
        assert a.get('/api/state').status_code == 200 and b.get('/api/state').status_code == 401
        assert a.post('/api/auth/logout-all').status_code == 200 and a.get('/api/state').status_code == 401


def test_account_and_ip_rate_limits_survive_restart(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings), headers=HEADERS) as c:
        for _ in range(15):
            assert c.post('/api/auth/login', json={'email': 'unknown@example.com', 'password': 'wrong'}).status_code == 401
        assert c.post('/api/auth/login', json={'email': 'unknown@example.com', 'password': 'wrong'}).status_code == 429
    with TestClient(create_app(settings), headers=HEADERS) as c:
        assert c.post('/api/auth/login', json={'email': 'unknown@example.com', 'password': 'wrong'}).status_code == 429


def test_production_fails_closed_and_cookie_is_secure(tmp_path):
    with pytest.raises(ValueError): settings_at(tmp_path, public_url='https://vault.example.com').__class__(environment='production')
    settings = Settings(data=tmp_path/'data', jobs=tmp_path/'jobs', secrets=tmp_path/'secrets', environment='production', public_url='https://vault.example.com', smtp_host='smtp.example.com', smtp_port=587, smtp_tls='starttls', mail_from='noreply@example.com', support_email='support@example.com')
    with TestClient(create_app(settings), base_url=settings.public_url, headers={**HEADERS, 'Origin': settings.public_url}) as c:
        register_pending(c)
        response = c.post('/api/auth/verify-email', json={'token': token_for(settings, 'verify')})
        cookie = response.headers['set-cookie']
        assert '__Host-lv_session=' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'Domain=' not in cookie
        assert c.get('/api/state').status_code == 200
        assert c.post('/api/courses', json={'title': 'No'}, headers={'Origin': 'https://evil.example.com'}).status_code == 403


def test_durable_mail_delivery_uses_canonical_url_and_clears_secret(tmp_path, monkeypatch):
    settings = settings_at(tmp_path); settings.verify_email = True
    with TestClient(create_app(settings), headers=HEADERS) as c:
        register_pending(c)
    from backend import mailer
    calls = []
    monkeypatch.setattr(mailer, 'send', lambda configuration, row: calls.append(json.loads(cipher(configuration).decrypt(row['payload'].encode()))))
    assert deliver_one(settings) and len(calls) == 1
    assert not deliver_one(settings)
    with connect(settings) as db:
        row = db.execute('SELECT * FROM mail_outbox').fetchone()
        assert row['sent_at'] and row['payload'] == ''


def test_interrupted_account_erasure_can_be_retried_after_restart(tmp_path):
    settings = settings_at(tmp_path)
    with TestClient(create_app(settings), headers=HEADERS) as c:
        c.post('/api/auth/register', json={'email': 'person@example.com', 'name': 'User', 'password': PASSWORD})
        with connect(settings) as db:
            db.execute('UPDATE users SET disabled=2')  # Process died during profile purge.
        assert c.get('/api/state').status_code == 409
    with TestClient(create_app(settings), headers=HEADERS) as c:
        assert c.post('/api/auth/login', json={'email': 'person@example.com', 'password': PASSWORD}).status_code == 200
        assert c.request('DELETE', '/api/account', json={'password': PASSWORD}).status_code == 200
