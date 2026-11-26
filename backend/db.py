import contextlib
import json
import logging
import re
import secrets
import sqlite3
import time
import uuid

from .config import Settings


log = logging.getLogger(__name__)


class StorageConnection(sqlite3.Connection):
    pending_file_cleanup = False


_ASSET_REFERENCE = re.compile(r'/api/assets/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?![0-9a-z-])')


def note_asset_ids(markdown):
    return set(_ASSET_REFERENCE.findall(markdown))


def replace_note_asset_ids(markdown, replacements):
    return _ASSET_REFERENCE.sub(lambda match: '/api/assets/' + replacements.get(match[1], match[1]), markdown)


def note_asset_reference(db, course_id, asset_id):
    for note in db.execute('SELECT id,markdown FROM notes WHERE course_id=? AND instr(markdown,?)>0',
                           (course_id, '/api/assets/' + asset_id)):
        if asset_id in note_asset_ids(note['markdown']):
            return note
    return db.execute("""SELECT n.id FROM note_revision_assets ra JOIN notes n ON n.id=ra.note_id
                         WHERE ra.asset_id=? AND n.course_id=? LIMIT 1""", (asset_id, course_id)).fetchone()


def now():
    return int(time.time() * 1000)


def uid():
    return str(uuid.uuid4())


@contextlib.contextmanager
def connect(settings: Settings):
    db = sqlite3.connect(settings.data / 'lecturevault.sqlite3', timeout=15, factory=StorageConnection)
    db.uncommitted_files = set()
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    try:
        with db:
            yield db
    except BaseException:
        if db.uncommitted_files:
            run_file_cleanup(db, settings, db.uncommitted_files)
        raise
    else:
        if db.pending_file_cleanup:
            run_file_cleanup(db, settings)
    finally:
        db.close()


def run_file_cleanup(db, settings, uncommitted_files=()):
    from .media import collect_file_cleanup, queue_file_cleanup
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for path in uncommitted_files:
                queue_file_cleanup(db, path)
            collect_file_cleanup(db, settings)
    except (OSError, sqlite3.Error):
        log.exception('Asset file cleanup failed')


def initialize(settings: Settings):
    for directory in [settings.data, settings.data / 'assets', settings.jobs / 'blobs', settings.secrets]:
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
        CREATE TABLE IF NOT EXISTS course_invites (
          course_id TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
          digest TEXT NOT NULL UNIQUE, role TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS members (
          course_id TEXT REFERENCES courses(id), user_id TEXT NOT NULL,
          role TEXT CHECK(role IN ('editor','viewer')) NOT NULL,
          PRIMARY KEY(course_id,user_id));
        CREATE TABLE IF NOT EXISTS notes (
          id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id) NOT NULL,
          title TEXT NOT NULL, markdown TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          job_id TEXT UNIQUE, is_mock INTEGER NOT NULL DEFAULT 0, created_by TEXT, updated_by TEXT);
        CREATE TABLE IF NOT EXISTS note_revisions (
          note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, revision INTEGER NOT NULL,
          title TEXT NOT NULL, markdown TEXT NOT NULL, updated_at INTEGER NOT NULL, edited_by TEXT, size INTEGER NOT NULL,
          PRIMARY KEY(note_id,revision));
        CREATE TABLE IF NOT EXISTS note_revision_assets (
          note_id TEXT NOT NULL, revision INTEGER NOT NULL, asset_id TEXT NOT NULL,
          PRIMARY KEY(note_id,revision,asset_id),
          FOREIGN KEY(note_id,revision) REFERENCES note_revisions(note_id,revision) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS note_revision_asset ON note_revision_assets(asset_id);
        CREATE TABLE IF NOT EXISTS note_categories (
          note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE, path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS category_folders (
          course_id TEXT REFERENCES courses(id) ON DELETE CASCADE NOT NULL,
          path TEXT NOT NULL, PRIMARY KEY(course_id,path));
        CREATE TABLE IF NOT EXISTS course_icons (
          course_id TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE, icon TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id) NOT NULL,
          created_by TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
          created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
          attempt INTEGER NOT NULL DEFAULT 0, note_id TEXT, error TEXT, error_code TEXT);
        CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, course_id TEXT REFERENCES courses(id) NOT NULL,
          note_id TEXT, job_id TEXT, name TEXT NOT NULL, mime TEXT NOT NULL,
          path TEXT NOT NULL, size INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS media_results (
          course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          digest TEXT NOT NULL, operation TEXT NOT NULL, variant TEXT NOT NULL,
          version TEXT NOT NULL, result BLOB NOT NULL, touched INTEGER NOT NULL,
          PRIMARY KEY(course_id,digest,operation,variant));
        CREATE INDEX IF NOT EXISTS media_result_age ON media_results(touched);
        CREATE TABLE IF NOT EXISTS pdf_text_results (
          digest TEXT PRIMARY KEY, version TEXT NOT NULL,
          result BLOB NOT NULL, touched INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS file_leases (
          id TEXT PRIMARY KEY, path TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS file_lease_path ON file_leases(path,expires);
        CREATE TABLE IF NOT EXISTS editor_storage_limits (
          owner_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          percent INTEGER NOT NULL CHECK(percent BETWEEN 0 AND 100));
        CREATE TABLE IF NOT EXISTS speech_results (
          audio_sha256 TEXT NOT NULL, language TEXT NOT NULL, engine TEXT NOT NULL,
          text_sha256 TEXT NOT NULL,
          PRIMARY KEY(audio_sha256,language,engine));
        CREATE INDEX IF NOT EXISTS speech_text ON speech_results(text_sha256);
        CREATE TABLE IF NOT EXISTS transcript_recognition (
          asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
          audio_sha256 TEXT NOT NULL, language TEXT NOT NULL, engine TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS transcript_recognition_key ON transcript_recognition(audio_sha256,language,engine);
        CREATE TABLE IF NOT EXISTS media_garbage (path TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS sessions (
          digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS provider_links (user_id TEXT PRIMARY KEY REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS imports (
          user_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(user_id,digest));
        CREATE INDEX IF NOT EXISTS note_course ON notes(course_id);
        CREATE INDEX IF NOT EXISTS job_course ON jobs(course_id);
        CREATE INDEX IF NOT EXISTS job_sender_started ON jobs(created_by,started_at);
        ''')
        db.execute('BEGIN IMMEDIATE')
        note_columns = {row['name'] for row in db.execute('PRAGMA table_info(notes)')}
        if 'created_by' not in note_columns:
            db.execute('ALTER TABLE notes ADD COLUMN created_by TEXT')
        if 'updated_by' not in note_columns:
            db.execute('ALTER TABLE notes ADD COLUMN updated_by TEXT')
        db.execute("""UPDATE notes SET created_by=coalesce(
            (SELECT created_by FROM jobs WHERE id=notes.job_id AND course_id=notes.course_id),
            (SELECT owner_id FROM courses WHERE id=notes.course_id))
            WHERE created_by IS NULL""")
        asset_columns = {row['name'] for row in db.execute('PRAGMA table_info(assets)')}
        if 'sha256' not in asset_columns:
            db.execute('ALTER TABLE assets ADD COLUMN sha256 TEXT')
        if 'transcript_language' not in asset_columns:
            db.execute('ALTER TABLE assets ADD COLUMN transcript_language TEXT')
        if 'legacy_source_sha256' not in asset_columns:
            db.execute('ALTER TABLE assets ADD COLUMN legacy_source_sha256 TEXT')
            db.execute('DROP INDEX IF EXISTS transcripts_source')
            # The old hash identified the uploaded video, not the visible text.
            db.execute("""UPDATE assets SET legacy_source_sha256=sha256,sha256=NULL
                WHERE transcript_language IS NOT NULL""")
        db.execute('CREATE INDEX IF NOT EXISTS asset_content ON assets(course_id,sha256,mime)')
        db.execute('CREATE INDEX IF NOT EXISTS asset_blob_content ON assets(sha256,mime,size)')
        db.execute('CREATE INDEX IF NOT EXISTS asset_path ON assets(path)')
        db.execute('''CREATE TRIGGER IF NOT EXISTS collect_speech_identity AFTER DELETE ON assets
            WHEN OLD.mime='text/plain' AND OLD.sha256 IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM assets WHERE sha256=OLD.sha256 AND mime='text/plain')
            BEGIN
                DELETE FROM speech_results WHERE text_sha256=OLD.sha256;
            END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS collect_pdf_text AFTER DELETE ON assets
            WHEN OLD.mime='application/pdf' AND OLD.sha256 IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM assets WHERE sha256=OLD.sha256 AND mime='application/pdf')
            BEGIN
                DELETE FROM pdf_text_results WHERE digest=OLD.sha256;
                DELETE FROM media_results WHERE digest=OLD.sha256 AND operation='/slides';
            END''')
        db.execute("DELETE FROM media_results WHERE operation!='/slides' OR variant!=''")
        db.execute('''INSERT OR IGNORE INTO pdf_text_results(digest,version,result,touched)
            SELECT digest,version,result,touched FROM media_results WHERE length(result)>0 ORDER BY touched DESC''')
        # Keep course-scoped reuse flags without duplicating the extracted text.
        db.execute("UPDATE media_results SET result=x'' WHERE length(result)>0")
        db.execute("CREATE INDEX IF NOT EXISTS job_transcript ON jobs(course_id,json_extract(payload,'$.transcriptAssetId'))")
        if 'created_by' not in {row['name'] for row in db.execute('PRAGMA table_info(assets)')}:
            db.execute('ALTER TABLE assets ADD COLUMN created_by TEXT')
            db.execute("""UPDATE assets SET created_by=coalesce(
                (SELECT created_by FROM jobs WHERE id=assets.job_id AND course_id=assets.course_id),
                (SELECT created_by FROM notes WHERE id=assets.note_id AND course_id=assets.course_id),
                (SELECT created_by FROM jobs WHERE course_id=assets.course_id
                    AND json_extract(payload,'$.transcriptAssetId')=assets.id ORDER BY created_at,id LIMIT 1),
                (SELECT owner_id FROM courses WHERE id=assets.course_id))""")
        if 'icon' not in {row['name'] for row in db.execute('PRAGMA table_info(category_folders)')}:
            db.execute("ALTER TABLE category_folders ADD COLUMN icon TEXT NOT NULL DEFAULT 'folder'")
        # Preserve old note-derived categories, including their intermediate parents.
        for row in db.execute('SELECT DISTINCT course_id,path FROM notes JOIN note_categories ON notes.id=note_id'):
            parts = row['path'].split('/')
            db.executemany('INSERT OR IGNORE INTO category_folders(course_id,path) VALUES (?,?)',
                           [(row['course_id'], '/'.join(parts[:i+1])) for i in range(len(parts))])
        columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
        for name in ['created_at', 'last_seen']:
            if name not in columns:
                db.execute(f'ALTER TABLE sessions ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id)')
        db.execute('CREATE INDEX IF NOT EXISTS mail_pending ON mail_outbox(sent_at,next_attempt)')



def course_json(row, role):
    return dict(id=row['id'], title=row['title'], workspace=row['workspace'], createdAt=row['created_at'], ownerId=row['owner_id'], role=role)


def note_json(row):
    return dict(id=row['id'], courseId=row['course_id'], title=row['title'], markdown=row['markdown'], origin=row['origin'], revision=row['revision'], createdAt=row['created_at'], updatedAt=row['updated_at'], jobId=row['job_id'], isMock=bool(row['is_mock']), createdBy=row['created_by'])


def job_json(row):
    return {**json.loads(row['payload']), **dict(id=row['id'], courseId=row['course_id'], createdBy=row['created_by'], status=row['status'], createdAt=row['created_at'], startedAt=row['started_at'], completedAt=row['completed_at'], attempt=row['attempt'], noteId=row['note_id'], error=row['error'], errorCode=row['error_code'])}
