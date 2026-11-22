"""Separated non-root media and no-tools Claude services. Never a shell gateway."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from contextlib import asynccontextmanager
from functools import lru_cache, wraps
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

def run(argv, *, cwd, timeout, on_progress=None, read_file=None):
    # Media parsers see one job only, not the other users' uploaded files.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        home, temporary = root / 'home', root / 'tmp'
        home.mkdir(); temporary.mkdir()
        command = [sys.executable, '-m', 'runner.sandbox', str(home), str(cwd), str(temporary)]
        if read_file:
            command.extend(['--read-file', str(read_file)])
        command.extend(argv)
        environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(temporary), 'PYTHONPATH': '/app', 'LANG': 'C.UTF-8', 'OMP_NUM_THREADS': '4'}
        return run_process(command, cwd=cwd, timeout=timeout, env=environment, on_progress=on_progress)

def serialized_media(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with media_lock:
            return function(*args, **kwargs)
    return wrapped


def job_file(job_id, filename, source_job_id=None):
    if not re.fullmatch(r'[0-9a-f-]{36}', job_id) or not re.fullmatch(r'[0-9a-f-]{36}\.(mp4|mov|mkv|webm|pdf)', filename):
        fail('INVALID_PATH', 'Некорректный идентификатор файла.')
    directory = JOBS / job_id
    if source_job_id and not re.fullmatch(r'[0-9a-f-]{36}', source_job_id):
        fail('INVALID_PATH', 'Некорректный идентификатор файла.')
    path = JOBS / (source_job_id or job_id) / filename
    if directory.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(JOBS.resolve()) or not path.is_file():
        fail('FILE_MISSING', 'Файл задания недоступен.')
    if not directory.is_dir():
        fail('FILE_MISSING', 'Недоступна папка задания.')
    return directory, path


@app.get('/health')
def health():
    return {'ok': True, 'kind': KIND, 'uid': os.geteuid()}


class MediaRequest(BaseModel):
    jobId: str = Field(max_length=36)
    sourceJobId: str | None = Field(default=None, max_length=36)
    cachedVersion: str | None = Field(default=None, max_length=64)


@lru_cache(maxsize=32)
def file_fingerprint(path, identity):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def engine_version(operation):
    # Package metadata covers dynamically linked codecs and Poppler/font updates.
    paths = [__file__, '/var/lib/dpkg/status']
    if operation == 'speech':
        paths += ['/usr/bin/ffprobe', '/usr/bin/ffmpeg', '/opt/whisper/bin/whisper-cli',
                  os.environ.get('WHISPER_MODEL', '/opt/models/ggml-tiny.bin')]
    else:
        paths += ['/usr/bin/pdfinfo', '/usr/bin/pdftotext', '/usr/bin/pdftoppm']
    digest = hashlib.sha256(operation.encode())
    for path in paths:
        stat = os.stat(path)
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_ctime_ns)
        digest.update(file_fingerprint(path, identity).encode())
    return digest.hexdigest()


def cached_output(operation, body, filename, process):
    if KIND != 'media':
        raise HTTPException(404)
    directory, source = job_file(body.jobId, filename, body.sourceJobId)
    version = engine_version(operation) if body.cachedVersion is not None else None
    if version and version == body.cachedVersion:
        return {'unchanged': True}
    # Only this work directory is writable. The source may belong to an earlier
    # job of the same course and is exposed as one read-only Landlock file rule.
    with tempfile.TemporaryDirectory(dir=directory) as scratch:
        result = process(body, Path(scratch), source)
    if version:
        result['cacheVersion'] = version
    return result


class MediaInput(MediaRequest):
    video: str = Field(max_length=60)
    language: str = 'ru'


@app.post('/transcribe', dependencies=[Depends(auth)])
@serialized_media
def transcribe(body: MediaInput):
    return cached_output('speech', body, body.video, recognize)


def recognize(body, directory, video):
    info = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration', '-of', 'json', str(video)], cwd=directory, timeout=30, read_file=video))
    duration = float(info.get('format', {}).get('duration', 0))
    if not 0 < duration <= 7200:
        fail('DURATION_LIMIT', 'Допустимо видео с длительностью до двух часов.')
    audio = directory / 'audio.wav'
    result = directory / 'transcript'
    output = result.with_suffix('.txt')
    output.unlink(missing_ok=True)
    try:
        run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-t', '7200', str(audio)], cwd=directory, timeout=300, read_file=video)
        language = body.language if body.language in ['ru', 'en', 'auto'] else 'ru'
        def progress(percent):
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as marker:
                marker.write(str(percent).encode())
            os.replace(marker.name, directory.parent / 'recognition.progress')
        progress(0)
        run(['/opt/whisper/bin/whisper-cli', '-m', os.environ.get('WHISPER_MODEL', '/opt/models/ggml-tiny.bin'), '-f', str(audio), '-l', language, '-t', '4', '-ng', '-pp', '-otxt', '-of', str(result)], cwd=directory, timeout=3600, on_progress=progress)
        if output.is_symlink() or not output.resolve().is_relative_to(directory.resolve()):
            fail('UNSAFE_OUTPUT', 'Обработчик создал недопустимый выходной файл.')
        with output.open(encoding='utf-8') as transcript:
            text = transcript.read(140001)
    finally:
        audio.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
    if len(text) > 140000:
        fail('TRANSCRIPT_LIMIT', 'Расшифровка превышает 140 000 символов. Разделите видео на более короткие записи.')
    return {'text': text, 'duration': duration}


class SlidesInput(MediaRequest):
    pdf: str = Field(max_length=60)


@app.post('/slides', dependencies=[Depends(auth)])
@serialized_media
def slides(body: SlidesInput):
    return cached_output('pdf-text', body, body.pdf, extract_slides)


def extract_slides(_body, directory, pdf):
    pages = pdf_pages(directory, pdf)
    text = run(['pdftotext', '-f', '1', '-l', '500', '-layout', str(pdf), '-'], cwd=directory, timeout=60, read_file=pdf)
    sections = [(f'[Слайд {i}] ', page.strip()) for i, page in enumerate(text.split('\f')[:pages], 1) if page.strip()]
    size = sum(len(label) + len(content) + 1 for label, content in sections)
    condensed = size > 40000
    if condensed:
        budget = (40000 - sum(len(label) + 1 for label, _ in sections)) // len(sections)
        sections = [(label, content[:budget]) for label, content in sections]
    return {'text': '\n'.join(label + content for label, content in sections), 'pages': pages, 'condensed': condensed}


def pdf_pages(directory, pdf):
    info = run(['pdfinfo', str(pdf)], cwd=directory, timeout=20, read_file=pdf)
    match = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not match or not 1 <= int(match.group(1)) <= 500:
        fail('PDF_PAGE_LIMIT', 'Не удалось прочитать число страниц PDF, либо презентация превышает 500 страниц.')
    return int(match.group(1))


class SlidePreview(SlidesInput):
    page: int = Field(default=0, ge=0, le=500)


preview_slot = threading.Semaphore(1)


def slide_jpeg(directory, pdf, page):
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        target = Path(temp) / 'page'
        run(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile', '-scale-to', '1250',
             '-jpeg', '-jpegopt', 'quality=80', str(pdf), str(target)], cwd=directory, timeout=25, read_file=pdf)
        image = target.with_suffix('.jpg')
        if image.is_symlink() or not image.is_file() or image.stat().st_size > 2_000_000:
            fail('PDF_PREVIEW', 'Не удалось показать страницу PDF.')
        return base64.b64encode(image.read_bytes()).decode('ascii')


@app.post('/slide-preview', dependencies=[Depends(auth)])
def slide_preview(body: SlidePreview):
    with preview_slot:
        return cached_output('pdf-jpeg', body, body.pdf, render_preview)


def render_preview(body, directory, pdf):
    if pdf.suffix != '.pdf':
        raise HTTPException(404)
    pages = pdf_pages(directory, pdf)
    if body.page > pages:
        fail('PDF_PAGE', 'Такой страницы нет в презентации.')
    return {'pages': pages, 'jpeg': slide_jpeg(directory, pdf, body.page) if body.page else None}



if KIND == 'providers':
    from .provider import router
    app.include_router(router)
