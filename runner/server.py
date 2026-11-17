"""Separated non-root media and no-tools Claude services. Never a shell gateway."""
import json
import os
import re
from pathlib import Path
from contextlib import asynccontextmanager
from functools import wraps
import threading
import tempfile
import sys

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from .common import auth, fail, run as run_process

KIND = os.environ.get('RUNNER_KIND', 'media')
JOBS = Path('/jobs')
@asynccontextmanager
async def lifespan(_app):
    from .sandbox import abi
    if os.geteuid() == 0 or abi() < 3:
        raise RuntimeError('Non-root Linux Landlock ABI 3 is required')
    if KIND == 'providers':
        from .provider import PROFILES, secret_cipher
        from .sandbox import abi
        if os.geteuid() == 0 or abi() < 3:
            raise RuntimeError('Non-root Linux Landlock ABI 3 is required for provider isolation')
        PROFILES.mkdir(parents=True, exist_ok=True, mode=0o700)
        secret_cipher()
    yield

app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
media_lock = threading.Lock()

def run(argv, *, cwd, timeout):
    # Media parsers see one job only, not the other users' uploaded files.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        home, temporary = root / 'home', root / 'tmp'
        home.mkdir(); temporary.mkdir()
        command = [sys.executable, '-m', 'runner.sandbox', str(home), str(cwd), str(temporary), *argv]
        environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(temporary), 'PYTHONPATH': '/app', 'LANG': 'C.UTF-8', 'OMP_NUM_THREADS': '4'}
        return run_process(command, cwd=cwd, timeout=timeout, env=environment)

def serialized_media(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with media_lock:
            return function(*args, **kwargs)
    return wrapped


def job_file(job_id, filename):
    if not re.fullmatch(r'[0-9a-f-]{36}', job_id) or not re.fullmatch(r'[0-9a-f-]{36}\.(mp4|mov|mkv|webm|pdf)', filename):
        fail('INVALID_PATH', 'Некорректный идентификатор файла.')
    directory = JOBS / job_id
    path = directory / filename
    if directory.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(JOBS.resolve()) or not path.is_file():
        fail('FILE_MISSING', 'Файл задания недоступен.')
    return directory, path


@app.get('/health')
def health():
    return {'ok': True, 'kind': KIND, 'uid': os.geteuid()}


class MediaInput(BaseModel):
    jobId: str = Field(max_length=36)
    video: str = Field(max_length=60)
    language: str = 'ru'


@app.post('/transcribe', dependencies=[Depends(auth)])
@serialized_media
def transcribe(body: MediaInput):
    if KIND != 'media':
        raise HTTPException(404)
    directory, video = job_file(body.jobId, body.video)
    info = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration', '-of', 'json', str(video)], cwd=directory, timeout=30))
    duration = float(info.get('format', {}).get('duration', 0))
    if not 0 < duration <= 7200:
        fail('DURATION_LIMIT', 'Допустимо видео с длительностью до двух часов.')
    audio = directory / 'audio.wav'
    try:
        run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-t', '7200', str(audio)], cwd=directory, timeout=300)
        language = body.language if body.language in ['ru', 'en', 'auto'] else 'ru'
        result = directory / 'transcript'
        run(['/opt/whisper/bin/whisper-cli', '-m', os.environ.get('WHISPER_MODEL', '/opt/models/ggml-tiny.bin'), '-f', str(audio), '-l', language, '-t', '4', '-ng', '-otxt', '-of', str(result)], cwd=directory, timeout=3600)
        output = result.with_suffix('.txt')
        if output.is_symlink() or not output.resolve().is_relative_to(directory.resolve()):
            fail('UNSAFE_OUTPUT', 'Обработчик создал недопустимый выходной файл.')
        with output.open(encoding='utf-8') as transcript:
            text = transcript.read(140001)
    finally:
        audio.unlink(missing_ok=True)
    if len(text) > 140000:
        fail('TRANSCRIPT_LIMIT', 'Расшифровка превышает 140 000 символов. Разделите видео на более короткие записи.')
    return {'text': text, 'duration': duration}


class SlidesInput(BaseModel):
    jobId: str = Field(max_length=36)
    pdf: str = Field(max_length=60)


@app.post('/slides', dependencies=[Depends(auth)])
@serialized_media
def slides(body: SlidesInput):
    if KIND != 'media':
        raise HTTPException(404)
    directory, pdf = job_file(body.jobId, body.pdf)
    info = run(['pdfinfo', str(pdf)], cwd=directory, timeout=20)
    count = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not count or int(count.group(1)) > 100:
        fail('PDF_PAGE_LIMIT', 'PDF должен содержать не более 100 страниц.')
    text = run(['pdftotext', '-f', '1', '-l', '100', '-layout', str(pdf), '-'], cwd=directory, timeout=60)
    if not text.strip():
        fail('PDF_NO_TEXT', 'PDF не содержит текстового слоя. Для этой версии нужны слайды с выделяемым текстом.')
    if len(text) > 40000:
        fail('PDF_TEXT_LIMIT', 'PDF содержит более 40 000 символов. Сократите презентацию перед повторной загрузкой.')
    return {'text': text, 'pages': int(count.group(1))}



if KIND == 'providers':
    from .provider import router
    app.include_router(router)
