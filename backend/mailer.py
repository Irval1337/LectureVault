"""Durable email outbox. One-time links are encrypted at rest and never logged."""
import json
import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from cryptography.fernet import Fernet

from .config import Settings
from .db import connect, now, uid

log = logging.getLogger('lecturevault.mailer')


def cipher(settings):
    return Fernet((settings.secrets / 'auth' / 'encryption-key').read_bytes())


def enqueue(settings, db, recipient, purpose, token=''):
    payload = cipher(settings).encrypt(json.dumps({'purpose': purpose, 'token': token}).encode()).decode()
    db.execute('INSERT INTO mail_outbox(id,recipient,payload,created_at) VALUES (?,?,?,?)', (uid(), recipient, payload, now()))


def send(settings, row):
    payload = json.loads(cipher(settings).decrypt(row['payload'].encode()))
    purpose = payload['purpose']
    message = EmailMessage()
    message['From'] = settings.mail_from
    message['To'] = row['recipient']
    if purpose == 'verify':
        message['Subject'] = 'Подтвердите email в LectureVault'
        text = 'Подтвердите создание аккаунта. Ссылка действует 24 часа.'
        link = settings.public_url + '/verify-email#token=' + payload['token']
    elif purpose == 'reset':
        message['Subject'] = 'Восстановление доступа к LectureVault'
        text = 'Чтобы задать новый пароль, откройте ссылку. Она действует 30 минут.'
        link = settings.public_url + '/reset-password#token=' + payload['token']
    else:
        message['Subject'] = 'Пароль LectureVault изменён'
        text = 'Пароль вашего аккаунта изменён. Если это сделали не вы, восстановите доступ.'
        link = settings.public_url + '/forgot-password'
    message.set_content(f'{text}\n\n{link}\n\nЕсли вы не запрашивали это действие, не переходите по ссылке.\nLectureVault\n')
    password = Path(settings.smtp_password_file).read_text() if settings.smtp_password_file else ''
    client = smtplib.SMTP_SSL if settings.smtp_tls == 'ssl' else smtplib.SMTP
    kwargs = {'host': settings.smtp_host, 'port': settings.smtp_port, 'timeout': 20}
    if settings.smtp_tls == 'ssl':
        kwargs['context'] = ssl.create_default_context()
    with client(**kwargs) as smtp:
        if settings.smtp_tls == 'starttls':
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            smtp.login(settings.smtp_user, password)
        smtp.send_message(message)


def deliver_one(settings):
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM mail_outbox WHERE sent_at IS NOT NULL AND sent_at<?', (now() - 7 * 86400_000,))
        db.execute('UPDATE mail_outbox SET claimed_at=NULL WHERE claimed_at<?', (now() - 120_000,))
        row = db.execute('SELECT * FROM mail_outbox WHERE sent_at IS NULL AND attempts<8 AND claimed_at IS NULL AND next_attempt<=? ORDER BY created_at LIMIT 1', (now(),)).fetchone()
        if not row:
            return False
        db.execute('UPDATE mail_outbox SET claimed_at=? WHERE id=?', (now(), row['id']))
    try:
        send(settings, row)
        with connect(settings) as db:
            db.execute("UPDATE mail_outbox SET sent_at=?,payload='',claimed_at=NULL WHERE id=?", (now(), row['id']))
        log.info('message=%s delivered', row['id'])
    except Exception:
        # SMTP servers can echo recipient data or URLs in exceptions. Do not log them.
        with connect(settings) as db:
            db.execute('UPDATE mail_outbox SET attempts=attempts+1,next_attempt=?,claimed_at=NULL WHERE id=?', (now() + min(300, 2 ** (row['attempts'] + 2)) * 1000, row['id']))
        log.warning('message=%s delivery_failed', row['id'])
    return True


def main():
    if os.geteuid() == 0:
        raise SystemExit('Mailer must run as a non-root user')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    settings = Settings()
    while True:
        if not deliver_one(settings):
            time.sleep(1)


if __name__ == '__main__':
    main()
