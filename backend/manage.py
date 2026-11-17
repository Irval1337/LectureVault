"""Operator commands. Secrets are read interactively, never from command arguments."""
import argparse
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile

from .config import Settings
from .db import connect, initialize


def backup(settings, output):
    """Run while api/worker/mailer are stopped; copies bytes, never parses media."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.exists():
        raise SystemExit('Output already exists; refusing to overwrite')
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / 'lecturevault.sqlite3'
        with sqlite3.connect(settings.data / 'lecturevault.sqlite3') as source, sqlite3.connect(snapshot) as target:
            source.backup(target)
            assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            for table in ['sessions', 'auth_tokens', 'mail_outbox']:
                target.execute(f'DELETE FROM {table}')
        with output.open('xb') as destination:
            os.chmod(output, 0o600)
            with tarfile.open(fileobj=destination, mode='w:gz') as archive:
                archive.add(snapshot, arcname='data/lecturevault.sqlite3')
                for path, name in [(settings.data / 'assets', 'data/assets'), (settings.jobs, 'jobs')]:
                    if path.exists():
                        archive.add(path, arcname=name, recursive=True)
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'createdAt': datetime.now(timezone.utc).isoformat(), 'providersIncluded': False, 'dataRoot': str(settings.data), 'jobsRoot': str(settings.jobs)}))
                archive.add(manifest, arcname='manifest.json')
    print('Backup created. It contains private data: store it on encrypted storage with mode 0600.')


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
        database = root / 'data' / 'lecturevault.sqlite3'
        with sqlite3.connect(database) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SystemExit('Invalid database')
            for asset in db.execute('SELECT id,path FROM assets').fetchall():
                old = Path(asset[1])
                for source_root, target_root in [(Path(metadata['dataRoot']), settings.data), (Path(metadata['jobsRoot']), settings.jobs)]:
                    if old.is_relative_to(source_root):
                        new = target_root / old.relative_to(source_root)
                        if not new.resolve().is_relative_to(target_root.resolve()):
                            raise SystemExit('Unsafe asset path')
                        db.execute('UPDATE assets SET path=? WHERE id=?', (str(new), asset[0])); break
                else:
                    raise SystemExit('Asset outside backup roots')
            # Backups do not restore sessions, reset links or provider claims.
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM auth_tokens')
            db.execute('DELETE FROM mail_outbox')
            from .auth import DUMMY_HASH
            db.execute('UPDATE users SET password_hash=?', (DUMMY_HASH,))
            db.execute("UPDATE jobs SET status='failed',error_code='RESTORED',error='Задание восстановлено из резервной копии. Подключите провайдера и повторите.' WHERE status NOT IN ('ready','failed','cancelled')")
        settings.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(root / 'data', settings.data, dirs_exist_ok=True)
        if (root / 'jobs').exists():
            shutil.copytree(root / 'jobs', settings.jobs, dirs_exist_ok=True)
        initialize(settings)
    print('Restore complete. Users must reset passwords by email. Provider profiles must use a new empty volume.')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check-config')
    sub.add_parser('smtp-password')
    sub.add_parser('backup').add_argument('--output', required=True)
    sub.add_parser('restore').add_argument('--input', required=True)
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


if __name__ == '__main__':
    main()
