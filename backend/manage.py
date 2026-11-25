"""Operator commands. Secrets are read interactively, never from command arguments."""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile

from .config import Settings
from .db import connect, initialize
from .media import BLOB_SUFFIXES, Upload, collect_storage_garbage, materialize_blob, regular_upload


def backup(settings, output):
    """Run while api/worker/mailer are stopped; copies bytes, never parses media."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.exists():
        raise SystemExit('Output already exists; refusing to overwrite')
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / 'lecturevault.sqlite3'
        with closing(sqlite3.connect(settings.data / 'lecturevault.sqlite3')) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
            # The archive contains only the main file. Flush WAL and remove old page contents.
            target.execute('PRAGMA journal_mode=DELETE')
            target.execute('PRAGMA secure_delete=ON')
            with target:
                for table in ['sessions', 'auth_tokens', 'mail_outbox', 'provider_links', 'rate_limits']:
                    target.execute(f'DELETE FROM {table}')
                if target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_leases'").fetchone():
                    target.execute('DELETE FROM file_leases')
                from .auth import DUMMY_HASH
                target.execute('UPDATE users SET password_hash=?', (DUMMY_HASH,))
            target.execute('VACUUM')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SystemExit('Invalid backup database')
        with output.open('xb') as destination:
            os.chmod(output, 0o600)
            with tarfile.open(fileobj=destination, mode='w:gz') as archive:
                archive.add(snapshot, arcname='data/lecturevault.sqlite3')
                for path, name in [(settings.data / 'assets', 'data/assets'), (settings.jobs, 'jobs')]:
                    if path.exists():
                        archive.add(path, arcname=name, recursive=True, filter=backup_member(path, name))
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'createdAt': datetime.now(timezone.utc).isoformat(), 'providersIncluded': False, 'dataRoot': str(settings.data), 'jobsRoot': str(settings.jobs)}))
                archive.add(manifest, arcname='manifest.json')
    print('Backup created. It contains private data: store it on encrypted storage with mode 0600.')


def backup_member(root, archive_root):
    def preserve_file(info):
        if info.islnk():
            # A pending file cleanup can briefly leave a second name for blob bytes.
            file = root / Path(info.name).relative_to(archive_root)
            info.size = file.lstat().st_size
            info.type = tarfile.REGTYPE
            info.linkname = ''
        return info
    return preserve_file


def restore(settings, source):
    """Empty volumes only. Python's data filter rejects traversal, links and devices."""
    if (settings.data.exists() and any(settings.data.iterdir())) or (settings.jobs.exists() and any(settings.jobs.iterdir())):
        raise SystemExit('Restore requires empty volumes; existing data is never overwritten')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with tarfile.open(source, 'r:gz') as archive:
            members = archive.getmembers()
            if any(member.issym() or member.islnk() or not (member.isfile() or member.isdir()) for member in members):
                raise SystemExit('Backup contains unsupported links/devices')
            if sum(m.size for m in members) > settings.total_quota + 1024**3:
                raise SystemExit('Backup exceeds configured storage limit')
            archive.extractall(root, filter='data')
        metadata = json.loads((root / 'manifest.json').read_text())
        if metadata.get('version') != 1:
            raise SystemExit('Unsupported backup version')
        roots = [(Path(metadata['dataRoot']), settings.data), (Path(metadata['jobsRoot']), settings.jobs)]
        database = root / 'data' / 'lecturevault.sqlite3'
        with sqlite3.connect(database) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SystemExit('Invalid database')
            for asset_id, path in db.execute('SELECT id,path FROM assets').fetchall():
                db.execute('UPDATE assets SET path=? WHERE id=?', (relocate(path, roots), asset_id))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_leases'").fetchone():
                db.execute('DELETE FROM file_leases')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_garbage'").fetchone():
                garbage = db.execute('SELECT path FROM media_garbage').fetchall()
                db.execute('DELETE FROM media_garbage')
                db.executemany('INSERT OR IGNORE INTO media_garbage(path) VALUES (?)',
                               [(relocate(path, roots),) for path, in garbage])
            # Backups do not restore sessions, reset links or provider claims.
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM auth_tokens')
            db.execute('DELETE FROM mail_outbox')
            db.execute('DELETE FROM provider_links')
            db.execute('DELETE FROM rate_limits')
            from .auth import DUMMY_HASH
            db.execute('UPDATE users SET password_hash=?', (DUMMY_HASH,))
            db.execute("UPDATE jobs SET status='failed',error_code='RESTORED',error='Задание восстановлено из резервной копии. Подключите провайдера и повторите.' WHERE status NOT IN ('ready','failed','cancelled')")
        settings.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(root / 'data', settings.data, dirs_exist_ok=True)
        if (root / 'jobs').exists():
            shutil.copytree(root / 'jobs', settings.jobs, dirs_exist_ok=True)
        initialize(settings)
        collect_storage_garbage(settings)
    print('Restore complete. Users must reset passwords by email. Provider profiles must use a new empty volume.')


def relocate(path, roots):
    old = Path(path)
    for source_root, target_root in roots:
        if old.is_relative_to(source_root):
            new = target_root / old.relative_to(source_root)
            if new.resolve().is_relative_to(target_root.resolve()):
                return str(new)
            break
    raise SystemExit('Unsafe asset path in backup')


def deduplicate_storage(settings, dry_run=False):
    """Inspect the files with API and worker stopped. Apply one content group at a time."""
    if not (settings.data / 'lecturevault.sqlite3').is_file():
        raise SystemExit('No LectureVault storage database found')
    with connect(settings) as db:
        rows = db.execute('SELECT DISTINCT mime,path FROM assets ORDER BY mime,path').fetchall()
    groups = defaultdict(list)
    skipped = 0
    for row in rows:
        mime, path = row['mime'], Path(row['path'])
        if mime not in BLOB_SUFFIXES:
            continue
        root = settings.jobs if mime == 'application/pdf' else settings.data / 'assets'
        if not regular_upload(path, root):
            skipped += 1
            continue
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            size = os.fstat(stream.fileno()).st_size
        groups[(mime, digest, size)].append(path)
    duplicates = sum(len(paths) - 1 for paths in groups.values())
    bytes_saved = sum((len(paths) - 1) * key[2] for key, paths in groups.items())
    if not dry_run:
        initialize(settings)
        for (mime, digest, size), paths in groups.items():
            with connect(settings) as db:
                db.execute('BEGIN IMMEDIATE')
                db.executemany('UPDATE assets SET sha256=?,size=? WHERE mime=? AND path=?',
                               [(digest, size, mime, str(path)) for path in paths])
                item = Upload('', '', mime, str(paths[0]), size, digest)
                materialize_blob(db, settings, item)
        collect_storage_garbage(settings)
    action = 'Dry run' if dry_run else 'Storage deduplicated'
    print(f'{action}. {duplicates} duplicate files ({bytes_saved} bytes), {skipped} missing or unsafe files skipped.')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check-config')
    sub.add_parser('smtp-password')
    sub.add_parser('backup').add_argument('--output', required=True)
    sub.add_parser('restore').add_argument('--input', required=True)
    sub.add_parser('deduplicate-storage', help='Consolidate stored PDFs, images and transcripts while offline').add_argument(
        '--dry-run', action='store_true', help='Report duplicates without changing storage')
    args = parser.parse_args()
    settings = Settings()
    if os.geteuid() == 0:
        raise SystemExit('Run operator commands as the application UID 10001')
    if args.command == 'smtp-password':
        initialize(settings)
        password = getpass('SMTP password (not echoed): ')
        if not password:
            raise SystemExit('Empty password')
        path = settings.secrets / 'auth' / 'smtp-password'
        temporary = path.with_suffix('.pending')
        temporary.write_text(password)
        temporary.chmod(0o600)
        temporary.replace(path)
        print('SMTP secret saved in the protected volume.')
    elif args.command == 'check-config':
        if settings.production and settings.smtp_user and not Path(settings.smtp_password_file).is_file():
            raise SystemExit('Missing SMTP secret. Run smtp-password first.')
        print('Configuration valid:', settings.environment, settings.public_url)
    elif args.command == 'backup':
        backup(settings, args.output)
    elif args.command == 'restore':
        restore(settings, args.input)
    elif args.command == 'deduplicate-storage':
        deduplicate_storage(settings, args.dry_run)


if __name__ == '__main__':
    main()
