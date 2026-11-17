import hmac
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from fastapi import Header, HTTPException

def auth(x_runner_key: str = Header(default='')):
    key = Path('/secrets/runner-key')
    if not key.is_file() or not hmac.compare_digest(key.read_text(), x_runner_key):
        raise HTTPException(403, 'Forbidden')
    if os.geteuid() == 0:
        raise HTTPException(503, {'code': 'ROOT_FORBIDDEN', 'message': 'Обработчик не может работать от root.'})


def fail(code, message):
    raise HTTPException(422, {'code': code, 'message': message})


def run(argv, *, cwd, timeout, text_input=None, allow_failure=False, env=None):
    # Static executable/options, bounded input. A process group is killed on timeout.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if text_input is not None else subprocess.DEVNULL, stdout=output, stderr=errors, start_new_session=True, env=env if env is not None else {**os.environ, 'OMP_NUM_THREADS': '4'})
        try:
            process.communicate(text_input.encode() if text_input is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            fail('TIMEOUT', 'Превышено время обработки. Можно повторить задание.')
        output.seek(0)
        result = output.read(1_000_000).decode('utf-8', errors='replace')
        if process.returncode and not allow_failure:
            fail('CLI_FAILED', 'Локальный инструмент завершился с ошибкой. Проверьте формат источника и настройку обработчика.')
        return result
