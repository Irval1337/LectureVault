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
import wave
import time
from collections import OrderedDict

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from backend import transcript as transcription
from .common import auth, fail, run as run_process

KIND = os.environ.get('RUNNER_KIND', 'media')
JOBS = Path('/jobs')
WHISPER_MODEL = os.environ.get('WHISPER_MODEL', '/opt/models/ggml-base.bin')
WHISPER_VAD = '/opt/models/ggml-silero-v5.1.2.bin'
MAX_TRANSCRIPT_CHARS = 1_000_000
MAX_TRANSCRIPT_BYTES = MAX_TRANSCRIPT_CHARS * 4
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

def run(argv, *, cwd, timeout, on_progress=None, read_file=None, output_limit=1_000_000):
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
        return run_process(command, cwd=cwd, timeout=timeout, env=environment, on_progress=on_progress, output_limit=output_limit)

def serialized_media(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with media_lock:
            return function(*args, **kwargs)
    return wrapped


def job_file(job_id, filename, source_job_id=None):
    blob = source_job_id == 'blobs'
    pattern = r'[a-f0-9]{64}\.pdf' if blob else r'[0-9a-f-]{36}\.(mp4|mov|mkv|webm|pdf)'
    if not re.fullmatch(r'[0-9a-f-]{36}', job_id) or not re.fullmatch(pattern, filename):
        fail('INVALID_PATH', 'Некорректный идентификатор файла.')
    directory = JOBS / job_id
    if source_job_id and not blob and not re.fullmatch(r'[0-9a-f-]{36}', source_job_id):
        fail('INVALID_PATH', 'Некорректный идентификатор файла.')
    path = JOBS / (source_job_id or job_id) / filename
    if directory.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(JOBS.resolve()) or not path.is_file():
        fail('FILE_MISSING', 'Файл задания недоступен.')
    if filename.endswith('.pdf'):
        return None, path
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
                  WHISPER_MODEL, WHISPER_VAD, transcription.__file__]
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
    prepareAudio: bool = False
    preparedAudio: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


@app.post('/transcribe', dependencies=[Depends(auth)])
@serialized_media
def transcribe(body: MediaInput):
    if KIND != 'media':
        raise HTTPException(404)
    if body.video.endswith('.pdf'):
        fail('INVALID_PATH', 'Некорректный идентификатор видео.')
    directory, video = job_file(body.jobId, body.video, body.sourceJobId)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        scratch = Path(temporary)
        audio = scratch / 'audio.wav'
        prepared = directory / 'recognition.wav'
        if body.preparedAudio:
            if prepared.is_symlink() or not prepared.is_file():
                fail('FILE_MISSING', 'Подготовленная звуковая дорожка недоступна.')
            os.replace(prepared, audio)
            digest, duration = pcm_identity(audio)
            if digest != body.preparedAudio:
                fail('AUDIO_CHANGED', 'Подготовленная звуковая дорожка изменилась.')
            version = engine_version('speech')
            return {'text': recognize(body, scratch, audio), 'duration': duration,
                    'audioSha256': digest, 'engineVersion': version}
        duration = normalize_audio(scratch, video, audio)
        if body.prepareAudio:
            digest, _ = pcm_identity(audio)
            version = engine_version('speech')
            os.replace(audio, prepared)
            return {'audioSha256': digest, 'engineVersion': version, 'duration': duration}
        return {'text': recognize(body, scratch, audio), 'duration': duration}


def normalize_audio(directory, video, audio):
    info = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration', '-of', 'json', str(video)], cwd=directory, timeout=30, read_file=video))
    duration = float(info.get('format', {}).get('duration', 0))
    if not 0 < duration <= 7200:
        fail('DURATION_LIMIT', 'Допустимо видео с длительностью до двух часов.')
    run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,pipe',
         '-i', str(video), '-map', '0:a:0', '-map_metadata', '-1', '-vn', '-ac', '1', '-ar', '16000',
         '-c:a', 'pcm_s16le', '-t', '7200', str(audio)], cwd=directory, timeout=300, read_file=video)
    return duration


def pcm_identity(audio):
    if audio.is_symlink() or not audio.is_file():
        fail('INVALID_AUDIO', 'Не удалось подготовить звуковую дорожку.')
    digest = hashlib.sha256()
    try:
        with wave.open(str(audio), 'rb') as stream:
            frames = stream.getnframes()
            if ((stream.getnchannels(), stream.getframerate(), stream.getsampwidth()) != (1, 16000, 2)
                    or not 0 < frames <= 7200 * 16000):
                fail('INVALID_AUDIO', 'Не удалось подготовить звуковую дорожку.')
            remaining = frames
            while remaining:
                chunk = stream.readframes(min(remaining, 32768))
                if not chunk or len(chunk) % 2:
                    fail('INVALID_AUDIO', 'Подготовленная звуковая дорожка повреждена.')
                digest.update(chunk)
                remaining -= len(chunk) // 2
    except (wave.Error, EOFError, OSError):
        fail('INVALID_AUDIO', 'Не удалось прочитать звуковую дорожку.')
    return digest.hexdigest(), frames / 16000


def recognize(body, directory, audio):
    result = directory / 'transcript'
    output = result.with_suffix('.txt')
    language = body.language if body.language in ['ru', 'en', 'auto'] else 'ru'

    def progress(percent):
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as marker:
            marker.write(str(percent).encode())
        os.replace(marker.name, directory.parent / 'recognition.progress')

    progress(0)
    run(['/opt/whisper/bin/whisper-cli', '-m', WHISPER_MODEL, '--vad', '--vad-model', WHISPER_VAD,
         '--vad-speech-pad-ms', '200', '--max-context', '0', '-f', str(audio), '-l', language,
         '-t', '4', '-ng', '-pp', '-otxt', '-of', str(result)], cwd=directory, timeout=3600, on_progress=progress)
    # Whisper writes no output when the VAD finds no speech at all.
    if not output.exists() and not output.is_symlink():
        fail('NO_SPEECH', 'Whisper не обнаружил речи. Проверьте звуковую дорожку.')
    if output.is_symlink() or not output.is_file() or not output.resolve().is_relative_to(directory.resolve()):
        fail('UNSAFE_OUTPUT', 'Обработчик создал недопустимый выходной файл.')
    with output.open('rb') as transcript:
        raw = transcript.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        fail('TRANSCRIPT_LIMIT', 'Расшифровка слишком длинная. Разделите видео на более короткие записи.')
    text = transcription.clean_transcript(raw.decode('utf-8'))
    if len(text) > MAX_TRANSCRIPT_CHARS:
        fail('TRANSCRIPT_LIMIT', 'Расшифровка слишком длинная. Разделите видео на более короткие записи.')
    return text


class SlidesInput(MediaRequest):
    pdf: str = Field(max_length=68)


pdf_extract_slot = threading.BoundedSemaphore(1)


@app.post('/slides', dependencies=[Depends(auth)])
def slides(body: SlidesInput):
    with pdf_extract_slot:
        return cached_output('pdf-text', body, body.pdf, extract_slides)


def extract_slides(_body, directory, pdf):
    pages = pdf_pages(directory, pdf)
    limit = 4_000_000
    text = run(['pdftotext', '-f', '1', '-l', str(pages), '-layout', str(pdf), '-'],
               cwd=directory, timeout=60, read_file=pdf, output_limit=limit + 1)
    if len(text.encode('utf-8')) > limit:
        fail('PDF_TEXT_LIMIT', 'Текст презентации превышает 4 МБ. Разделите PDF на части.')
    return {'text': '\n\n'.join(f'[Слайд {i}] {page.strip()}'
                              for i, page in enumerate(text.split('\f')[:pages], 1) if page.strip()),
            'pages': pages, 'condensed': False}


def pdf_pages(directory, pdf):
    info = run(['pdfinfo', str(pdf)], cwd=directory, timeout=20, read_file=pdf)
    match = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not match or not 1 <= int(match.group(1)) <= 500:
        fail('PDF_PAGE_LIMIT', 'Не удалось прочитать число страниц PDF, либо презентация превышает 500 страниц.')
    return int(match.group(1))


class SlidePreview(SlidesInput):
    page: int = Field(default=0, ge=0, le=500)


preview_slot = threading.Semaphore(1)
preview_cache = OrderedDict()
PREVIEW_CACHE_BYTES = 32 * 1024 * 1024
PREVIEW_CACHE_SECONDS = 15 * 60
preview_bytes = 0


def slide_jpeg(directory, pdf, page):
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        target = Path(temp) / 'page'
        run(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile', '-scale-to', '1250',
             '-jpeg', '-jpegopt', 'quality=80', str(pdf), str(target)], cwd=directory, timeout=25, read_file=pdf)
        image = target.with_suffix('.jpg')
        if image.is_symlink() or not image.is_file() or image.stat().st_size > 2_000_000:
            fail('PDF_PREVIEW', 'Не удалось показать страницу PDF.')
        return image.read_bytes()


@app.post('/slide-preview', dependencies=[Depends(auth)])
def slide_preview(body: SlidePreview):
    return cached_output('pdf-jpeg', body.model_copy(update={'cachedVersion': None}), body.pdf, render_preview)


def render_preview(body, directory, pdf):
    if pdf.suffix != '.pdf':
        raise HTTPException(404)
    stat = pdf.stat()
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_ctime_ns)
    source = pdf.stem if pdf.parent.name == 'blobs' else file_fingerprint(str(pdf), identity)
    with preview_slot:
        pages, _ = cached_preview((source, 0), lambda: (pdf_pages(directory, pdf), b''))
        if body.page > pages:
            fail('PDF_PAGE', 'Такой страницы нет в презентации.')
        if not body.page:
            return {'pages': pages, 'jpeg': None}
        _, jpeg = cached_preview((source, body.page), lambda: (pages, slide_jpeg(directory, pdf, body.page)))
        return {'pages': pages, 'jpeg': base64.b64encode(jpeg).decode('ascii')}


def cached_preview(key, render):
    global preview_bytes
    timestamp = time.monotonic()
    cached = preview_cache.pop(key, None)
    if cached:
        if cached[0] > timestamp:
            preview_cache[key] = cached
            return cached[1:]
        preview_bytes -= len(cached[2])
    pages, jpeg = render()
    preview_cache[key] = (time.monotonic() + PREVIEW_CACHE_SECONDS, pages, jpeg)
    preview_bytes += len(jpeg)
    for expired in [key for key, cached in preview_cache.items() if cached[0] <= timestamp]:
        preview_bytes -= len(preview_cache.pop(expired)[2])
    while preview_bytes > PREVIEW_CACHE_BYTES or len(preview_cache) > 2048:
        preview_bytes -= len(preview_cache.popitem(last=False)[1][2])
    return pages, jpeg



if KIND == 'providers':
    from .provider import router
    app.include_router(router)
