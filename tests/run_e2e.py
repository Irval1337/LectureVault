"""Run browser acceptance against a disposable Compose project, never user volumes."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    if os.environ.get('TEST_BASE_URL'):
        return subprocess.call(['npx', 'playwright', 'test', *sys.argv[1:]], cwd=ROOT)
    project = f'lecturevault-tests-{os.getpid()}'
    port, mail_port = free_port(), free_port()
    env = {**os.environ, 'LECTUREVAULT_PORT': str(port), 'LECTUREVAULT_MAIL_PORT': str(mail_port), 'LV_MAILPIT_URL': f'http://127.0.0.1:{mail_port}', 'LV_PUBLIC_URL': f'http://127.0.0.1:{port}', 'LV_ENV': 'test', 'LV_MOCK_SECONDS': '4', 'TEST_BASE_URL': f'http://127.0.0.1:{port}', 'LV_E2E_ISOLATED': '1'}
    command = ['docker', 'compose', '-p', project]
    log = ROOT / 'test-results' / 'compose.log'
    log.parent.mkdir(exist_ok=True)
    try:
        with log.open('w') as output:
            subprocess.run([*command, 'up', '-d', '--build', '--wait', '--wait-timeout', '180'], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT, check=True)
        for _ in range(40):
            try:
                with urllib.request.urlopen(env['TEST_BASE_URL'] + '/api/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.5)
        return subprocess.call(['npx', 'playwright', 'test', *sys.argv[1:]], cwd=ROOT, env=env)
    except subprocess.CalledProcessError:
        print(f'Compose failed. See {log}', file=sys.stderr)
        return 1
    finally:
        # Only resources labelled with this new, process-specific project name.
        subprocess.run([*command, 'down', '--volumes', '--rmi', 'local'], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(main())
