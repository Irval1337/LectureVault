import contextlib
import json
import secrets
import sqlite3
import time
import uuid

from .config import Settings


def now():
    return int(time.time() * 1000)


def uid():
    return str(uuid.uuid4())


@contextlib.contextmanager
def connect(settings: Settings):
    db = sqlite3.connect(settings.data / 'lecturevault.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(settings: Settings):
    for directory in [settings.data, settings.data / 'assets', settings.jobs, settings.secrets]:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for scope in ['media', 'providers', 'auth']:
        directory = settings.secrets / scope
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with (directory / 'runner-key').open('x') as file:
                file.write(secrets.token_urlsafe(48))
            (directory / 'runner-key').chmod(0o600)
        except FileExistsError:
            pass
    from cryptography.fernet import Fernet
    try:
        with (settings.secrets / 'auth' / 'encryption-key').open('xb') as key:
            key.write(Fernet.generate_key())
        (settings.secrets / 'auth' / 'encryption-key').chmod(0o600)
    except FileExistsError:
        pass
    with connect(settings) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
          password_hash TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS auth_tokens (
          digest TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) NOT NULL,
          purpose TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_limits (
          key TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS mail_outbox (
          id TEXT PRIMARY KEY, recipient TEXT NOT NULL, payload TEXT NOT NULL,
          created_at INTEGER NOT NULL, sent_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt INTEGER NOT NULL DEFAULT 0, claimed_at INTEGER);
        CREATE TABLE IF NOT EXISTS courses (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, workspace TEXT NOT NULL,
          owner_id TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS members (
          course_id TEXT REFERENCES courses(id), user_id TEXT NOT NULL,
          role TEXT CHECK(role IN ('editor','viewer')) NOT NULL,
          PRIMARY KEY(course_id,user_id));
        CREATE TABLE IF NOT EXISTS notes (
          id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id) NOT NULL,
          title TEXT NOT NULL, markdown TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          job_id TEXT UNIQUE, is_mock INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id) NOT NULL,
          created_by TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
          created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
          attempt INTEGER NOT NULL DEFAULT 0, note_id TEXT, error TEXT, error_code TEXT);
        CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id) NOT NULL,
          note_id TEXT, job_id TEXT, name TEXT NOT NULL, mime TEXT NOT NULL,
          path TEXT NOT NULL, size INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS provider_links (user_id TEXT PRIMARY KEY REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS imports (
          user_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(user_id,digest));
        CREATE INDEX IF NOT EXISTS note_course ON notes(course_id);
        CREATE INDEX IF NOT EXISTS job_course ON jobs(course_id);
        ''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
        for name in ['created_at', 'last_seen']:
            if name not in columns:
                db.execute(f'ALTER TABLE sessions ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id)')
        db.execute('CREATE INDEX IF NOT EXISTS mail_pending ON mail_outbox(sent_at,next_attempt)')



def course_json(row, role):
    return dict(id=row['id'], title=row['title'], workspace=row['workspace'], createdAt=row['created_at'], ownerId=row['owner_id'], role=role)


def note_json(row):
    return dict(id=row['id'], courseId=row['course_id'], title=row['title'], markdown=row['markdown'], origin=row['origin'], revision=row['revision'], createdAt=row['created_at'], updatedAt=row['updated_at'], jobId=row['job_id'], isMock=bool(row['is_mock']))


def job_json(row):
    return {**json.loads(row['payload']), **dict(id=row['id'], courseId=row['course_id'], createdBy=row['created_by'], status=row['status'], createdAt=row['created_at'], startedAt=row['started_at'], completedAt=row['completed_at'], attempt=row['attempt'], noteId=row['note_id'], error=row['error'], errorCode=row['error_code'])}
