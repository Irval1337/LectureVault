"""Multi-user provider broker. Each child CLI is confined to its own profile by Landlock."""
import base64
import binascii
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.providers import (ANTHROPIC_BASE, DETAILED_GENERATION_SECONDS, DETAILED_REQUIRED_TOKEN_BUDGET,
                               EFFORTS, MAX_NOTE_CHARS, MAX_SLIDE_IMAGE_BYTES, MAX_SLIDE_IMAGES, OPENAI_BASE,
                               PROVIDERS, valid_model, valid_api_key)
from . import compatible
from .common import auth, fail, run
from .note_prompts import SLIDE_DESCRIPTION, note_instructions
from .sandbox import abi

router = APIRouter(dependencies=[Depends(auth)])
PROFILES = Path(os.environ.get('LV_PROFILES', '/profiles'))
login_lock = threading.Lock()
locks_lock = threading.Lock()
profile_locks = {}
logins = {}
status_cache = {}
process_slots = threading.BoundedSemaphore(4)


class Identity(BaseModel):
    userId: str
    provider: str


def user_id(value):
    try:
        if str(uuid.UUID(value)) != value or uuid.UUID(value).version != 4:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(403, 'Invalid account') from None
    return value


def validate_identity(body):
    user_id(body.userId)
    if body.provider not in PROVIDERS:
        raise HTTPException(403, 'Unsupported provider')


def profile(identity, create=True):
    directory = PROFILES / user_id(identity)
    if directory.is_symlink():
        raise HTTPException(403)
    if create:
        for path in [directory, *(directory / name for name in ['home', 'work', 'tmp'])]:
            if path.is_symlink():
                raise HTTPException(403)
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def profile_lock(identity):
    user_id(identity)
    with locks_lock:
        return profile_locks.setdefault(identity, threading.RLock())


def secret_cipher():
    path = PROFILES / '.encryption-key'
    try:
        with path.open('xb') as file:
            file.write(Fernet.generate_key())
        path.chmod(0o600)
    except FileExistsError:
        pass
    return Fernet(path.read_bytes())


def cli_arguments(identity, argv, cwd=None):
    if abi() < 3:
        fail('ISOLATION_UNAVAILABLE', 'Сервис подключения требует Linux Landlock ABI 3.')
    directory = profile(identity)
    work = Path(cwd or directory / 'work')
    if not work.resolve().is_relative_to((directory / 'work').resolve()):
        raise HTTPException(403)
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(directory / 'home'), 'TMPDIR': str(directory / 'tmp'), 'PYTHONPATH': '/app', 'LANG': 'C.UTF-8', 'NO_COLOR': '1', 'BROWSER': 'true', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'OMP_NUM_THREADS': '2'}
    if argv[0] == 'claude' and connected(identity, 'claude'):
        environment['ANTHROPIC_API_KEY'] = api_config(identity, 'claude')['key']
    return [sys.executable, '-m', 'runner.sandbox', str(directory / 'home'), str(work), str(directory / 'tmp'), *argv], environment, work


def cli_run(identity, argv, *, cwd=None, detailed=False, **kwargs):
    command, environment, work = cli_arguments(identity, argv, cwd)
    if detailed and argv[0] == 'claude':
        environment['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] = str(DETAILED_REQUIRED_TOKEN_BUDGET)
        environment['API_TIMEOUT_MS'] = str(DETAILED_GENERATION_SECONDS * 1000)
    with process_slots:
        return run(command, cwd=work, env=environment, **kwargs)


def connected(identity, provider, force=False):
    directory = profile(identity, create=False)
    if not directory.is_dir():
        return False
    if PROVIDERS[provider]['authType'] == 'apiKey':
        if (directory / f'{provider}-key.enc').is_file():
            return True
        return provider == 'claude' and (directory / 'openai-key.enc').is_file() and api_config(identity)['baseUrl'] == ANTHROPIC_BASE
    cached = status_cache.get(identity)
    if cached and not force and cached[0] > time.monotonic():
        return cached[1]
    try:
        command, environment, work = cli_arguments(identity, ['codex', 'login', 'status'])
        with process_slots:
            result = subprocess.run(command, cwd=work, env=environment, capture_output=True, timeout=15)
        result = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        result = False
    status_cache[identity] = (time.monotonic() + 10, result)
    return result


def public_login(identity):
    entry = logins.get(identity)
    if not entry:
        return {'status': 'disconnected'}
    if time.monotonic() > entry['deadline'] and entry['process'].poll() is None:
        os.killpg(entry['process'].pid, signal.SIGKILL)
        entry['status'] = 'expired'
    if entry['process'].poll() is not None and entry['status'] in ['starting', 'waiting']:
        entry['status'] = 'finished' if entry['process'].returncode == 0 else 'failed'
    if entry['status'] in ('starting', 'waiting'):
        return {key: entry.get(key, '') for key in ('status', 'url', 'userCode')}
    return {'status': entry['status']}


def api_config(identity, provider='openai'):
    path = profile(identity, create=False) / f'{provider}-key.enc'
    if provider == 'claude' and not path.is_file():
        return api_config(identity)
    value = secret_cipher().decrypt(path.read_bytes()).decode()
    if value.startswith('{'):
        return json.loads(value)
    return {'baseUrl': ANTHROPIC_BASE if provider == 'claude' else OPENAI_BASE, 'key': value}


MODEL_CATALOG_TTL = 6 * 60 * 60
model_cache = {}
codex_specs = {}
last_public_status = {}
generating_status = {}


def restricted_spec(model):
    allowed = ('slug', 'display_name', 'supported_reasoning_levels', 'default_reasoning_level', 'support_verbosity',
               'default_verbosity', 'truncation_policy', 'context_window', 'max_context_window', 'supported_in_api',
               'priority', 'use_responses_lite', 'input_modalities', 'supports_image_detail_original')
    spec = {key: model[key] for key in allowed if key in model}
    spec.update(json.loads((Path(__file__).parent / 'codex-restrictions.json').read_text()))
    return spec


def clear_models(identity):
    last_public_status.pop(identity, None)
    codex_specs.pop(identity, None)
    for provider in PROVIDERS:
        model_cache.pop((identity, provider), None)


def catalog_for(identity, provider, force=False):
    key = (identity, provider)
    expires, previous, problem = model_cache.get(key, (0, [], ''))
    if not force and expires > time.monotonic():
        return previous, problem
    try:
        if provider == 'codex':
            items = []
            for flags in ([], ['--bundled']):
                try:
                    raw = json.loads(cli_run(identity, ['codex', 'debug', 'models', *flags], timeout=8, output_limit=10_000_000))
                    items = [{'id': m['slug'], 'name': m.get('display_name') or m['slug'],
                              'efforts': [r['effort'] for r in m.get('supported_reasoning_levels', []) if r.get('effort') in EFFORTS],
                              'supportsImages': 'image' in (m.get('input_modalities') or ()),
                              'contextTokens': compatible.model_limit(m.get('context_window'))}
                             for m in raw['models'] if valid_model(m.get('slug')) and m.get('visibility', 'list') == 'list']
                    if items:
                        codex_specs[identity] = {m["slug"]: restricted_spec(m) for m in raw["models"] if valid_model(m.get("slug")) and m.get("visibility", "list") == "list"}
                        break
                except (HTTPException, KeyError, TypeError, ValueError):
                    continue
        else:
            items = compatible.models(api_config(identity, provider))
        if not items:
            raise ValueError()
        model_cache[key] = (time.monotonic() + MODEL_CATALOG_TTL, items, '')
    except (HTTPException, ValueError, OSError):
        problem = 'Каталог временно недоступен.' if previous else 'Сервер не предоставил список моделей.'
        model_cache[key] = (time.monotonic() + 30, previous, problem)
    return model_cache[key][1:]


@router.post('/providers/status')
def provider_status(body: dict):
    identity = user_id(body.get('userId'))
    refresh = body.get('refresh') is True
    snapshot = generating_status.get(identity)
    if snapshot is not None:
        if refresh:
            raise HTTPException(409, {'code': 'CATALOG_BUSY', 'message': 'Модель готовит конспект. Обновите список после завершения.'})
        return snapshot
    with profile_lock(identity):
        entries = []
        for provider in PROVIDERS.values():
            name = provider['id']
            available = connected(identity, name)
            models, error = (catalog_for(identity, name, True) if refresh else catalog_for(identity, name)) if available else ([], '')
            item = {**provider, 'models': models, 'catalogError': error, 'connected': available,
                    'login': public_login(identity) if name == 'codex' else {'status': 'disconnected'}}
            if available and name == 'claude' and not (profile(identity, create=False) / 'claude-key.enc').exists():
                item['managedBy'] = 'openai'
            if available and name == 'openai':
                item['baseUrl'] = api_config(identity)['baseUrl']
            entries.append(item)
        result = {'providers': entries}
        if any(item['connected'] for item in entries) and not any(item['login'].get('userCode') for item in entries):
            last_public_status[identity] = result
        else:
            last_public_status.pop(identity, None)
        return result


def read_login(identity, entry):
    expecting_code = False
    for raw in entry['process'].stdout:
        line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', raw)
        if expecting_code and line.strip():
            if re.fullmatch(r'[A-Za-z0-9-]{4,32}', line.strip()):
                entry['userCode'] = line.strip()
            expecting_code = False
        if 'Enter this one-time code' in line:
            expecting_code = True
        for url in re.findall(r'https://[^\s\x1b]+', line):
            url = url.rstrip(').,')
            parsed = urlparse(url)
            if parsed.hostname == 'auth.openai.com' and not parsed.username and not parsed.password:
                entry['url'] = url
                entry['status'] = 'waiting'
    entry['process'].wait()
    entry['status'] = 'finished' if entry['process'].returncode == 0 else 'failed'
    status_cache.pop(identity, None)


@router.post('/providers/connect')
def connect_provider(body: Identity):
    validate_identity(body)
    if body.provider != 'codex':
        raise HTTPException(422, {'code': 'API_KEY_REQUIRED', 'message': 'Добавьте личный API-ключ в настройках.'})
    with profile_lock(body.userId), login_lock:
        existing = logins.get(body.userId)
        if existing and existing['process'].poll() is None and time.monotonic() < existing['deadline']:
            return public_login(body.userId)
        if connected(body.userId, 'codex', force=True):
            return {'status': 'finished'}
        if existing and existing['process'].poll() is None:
            os.killpg(existing['process'].pid, signal.SIGKILL)
            existing['process'].wait()
        active = sum(e['process'].poll() is None and e['deadline'] > time.monotonic() for e in logins.values())
        if active >= 3:
            raise HTTPException(429, {'code': 'LOGIN_BUSY', 'message': 'Сейчас выполняется несколько подключений. Повторите через минуту.'})
        command, environment, work = cli_arguments(body.userId, ['codex', '-c', 'cli_auth_credentials_store="file"', 'login', '--device-auth'])
        process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        entry = {'process': process, 'deadline': time.monotonic() + 600, 'status': 'starting', 'url': '', 'userCode': ''}
        logins[body.userId] = entry
        threading.Thread(target=read_login, args=(body.userId, entry), daemon=True).start()
        def expire():
            time.sleep(600)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
        threading.Thread(target=expire, daemon=True).start()
        return public_login(body.userId)


class ApiKey(Identity):
    apiKey: str = Field(default='', max_length=512)
    baseUrl: str = Field(default=OPENAI_BASE, max_length=500)
    workspace: str = Field(default='', pattern=r'^[a-zA-Z0-9_-]{0,100}$')


@router.post('/providers/key')
def save_key(body: ApiKey):
    validate_identity(body)
    if not valid_api_key(body.provider, body.apiKey):
        raise HTTPException(422, {'code': 'INVALID_KEY', 'message': 'Введите API-ключ выбранного провайдера.'})
    with profile_lock(body.userId):
        directory = profile(body.userId)
        target = directory / f'{body.provider}-key.enc'
        temporary = target.with_suffix('.pending')
        value = body.apiKey
        discovered = None
        if body.provider == 'openai':
            base = compatible.normalize(body.baseUrl)
            if not value and base not in compatible.local_bases():
                fail('API_KEY_REQUIRED', 'Введите ключ для внешнего API.')
            config = {'key': value, 'baseUrl': base, 'workspace': body.workspace}
            if 'baseUrl' in body.model_fields_set:
                try:
                    discovered = compatible.models(config)
                except HTTPException as error:
                    if error.detail.get('code') != 'API_MODELS_UNSUPPORTED':
                        raise
                    discovered = []
            value = json.dumps(config)
        elif body.provider == 'claude' and 'baseUrl' in body.model_fields_set:
            discovered = compatible.models({'baseUrl': ANTHROPIC_BASE, 'key': value})
        temporary.write_bytes(secret_cipher().encrypt(value.encode()))
        temporary.chmod(0o600)
        temporary.replace(target)
        clear_models(body.userId)
        if discovered is not None:
            model_cache[body.userId, body.provider] = (time.monotonic() + MODEL_CATALOG_TTL, discovered, '' if discovered else 'Сервер не публикует каталог моделей.')
    return {'ok': True}


@router.post('/providers/disconnect')
def disconnect_provider(body: Identity):
    validate_identity(body)
    with profile_lock(body.userId):
        clear_models(body.userId)
        entry = logins.pop(body.userId, None) if body.provider == 'codex' else None
        if entry and entry['process'].poll() is None:
            os.killpg(entry['process'].pid, signal.SIGKILL)
            entry['process'].wait()
        if profile(body.userId, create=False).exists():
            if body.provider == 'codex':
                cli_run(body.userId, ['codex', 'logout'], timeout=20, allow_failure=True)
                status_cache.pop(body.userId, None)
                if connected(body.userId, 'codex', force=True):
                    fail('DISCONNECT_FAILED', 'Не удалось отключить Codex. Повторите попытку.')
            else:
                (profile(body.userId) / f'{body.provider}-key.enc').unlink(missing_ok=True)
    return {'ok': True}


@router.post('/profiles/purge')
def purge_profile(body: dict):
    import shutil
    identity = user_id(body.get('userId'))
    with profile_lock(identity):
        clear_models(identity)
        entry = logins.pop(identity, None)
        if entry and entry['process'].poll() is None:
            os.killpg(entry['process'].pid, signal.SIGKILL)
            entry['process'].wait()
        directory = profile(identity, create=False)
        if directory.exists():
            shutil.rmtree(directory)
        status_cache.pop(identity, None)
    return {'ok': True}


class SlideImage(BaseModel):
    page: int = Field(ge=1, le=500)
    jpeg: str = Field(min_length=8, max_length=MAX_SLIDE_IMAGE_BYTES)

    @field_validator('jpeg')
    @classmethod
    def jpeg_only(cls, value):
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('Expected a base64 JPEG') from None
        if not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
            raise ValueError('Expected a JPEG image')
        return value

    @property
    def data_url(self):
        return f'data:image/jpeg;base64,{self.jpeg}'


class GenerationSelection(Identity):
    model: str = Field(max_length=180)
    apiBase: str | None = Field(default=None, max_length=500)
    effort: str = Field(max_length=12)
    slideImages: list[SlideImage] = Field(default_factory=list, max_length=MAX_SLIDE_IMAGES)

    @field_validator('slideImages')
    @classmethod
    def bounded_images(cls, value):
        if len({image.page for image in value}) != len(value):
            raise ValueError('Slide numbers must be unique')
        if sum(len(image.jpeg) for image in value) > MAX_SLIDE_IMAGE_BYTES:
            raise ValueError('Slide images exceed the request size limit')
        return sorted(value, key=lambda image: image.page)


class GenerateInput(GenerationSelection):
    targetWords: int | None = Field(default=None, ge=100, le=5000)
    slideLimit: int | None = Field(default=6, ge=0, le=24)
    pageCount: int = Field(default=0, ge=0, le=500)
    title: str = Field(max_length=120)
    preset: str = Field(max_length=20)
    length: str = Field(max_length=20)
    instructions: str = Field(max_length=4000)
    transcript: str = Field(max_length=1_000_000)
    slides: str = Field(default='', max_length=4_750_000)


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        identity = kwargs.get('body', args[0] if args else None).userId
        with profile_lock(identity):
            # Credential changes stay serialized. Other forms can read the same account's
            # last public catalog while its active job waits for a model response.
            if identity in last_public_status:
                generating_status[identity] = last_public_status[identity]
            try:
                return function(*args, **kwargs)
            finally:
                generating_status.pop(identity, None)
    return wrapped


def openai_response(key, model, effort, instructions, prompt, *, detailed=False, images=(), timeout=None):
    # A single chargeable request. Never follow redirects or forward a provider response to the browser.
    content = prompt
    if images:
        parts = [{'type': 'input_text', 'text': prompt}]
        for image in images:
            parts.extend([{'type': 'input_text', 'text': f'Слайд {image.page}'},
                          {'type': 'input_image', 'image_url': image.data_url, 'detail': 'auto'}])
        content = [{'role': 'user', 'content': parts}]
    payload = {'model': model, 'input': content, 'instructions': instructions, 'store': False, 'tools': []}
    if effort:
        payload['reasoning'] = {'effort': effort}
    if not detailed:
        payload['max_output_tokens'] = 16000
    try:
        response = httpx.post(
            'https://api.openai.com/v1/responses',
            headers={'Authorization': f'Bearer {key}'},
            json=payload, timeout=httpx.Timeout(timeout or (DETAILED_GENERATION_SECONDS if detailed else 220), connect=10),
            follow_redirects=False, trust_env=False,
        )
    except httpx.TimeoutException:
        fail('OPENAI_TIMEOUT', 'Время ожидания OpenAI истекло. Перед повтором проверьте расход в аккаунте OpenAI.')
    except httpx.RequestError:
        fail('OPENAI_UNAVAILABLE', 'Соединение с OpenAI API прервано. Проверьте статус сервиса и расход перед повтором.')
    if response.status_code == 401:
        fail('OPENAI_KEY', 'OpenAI отклонил API-ключ. Обновите ключ в настройках подключений.')
    if response.status_code in (403, 404):
        fail('OPENAI_ACCESS', 'Нет доступа к модели OpenAI. Проверьте права ключа или выберите другую модель.')
    if response.status_code == 429:
        fail('OPENAI_LIMIT', 'OpenAI сообщил о лимите запросов или баланса. Проверьте лимиты своего проекта.')
    if response.status_code in (400, 413, 422):
        try:
            limit = compatible.generation_limit(response.json().get('error'), response.status_code)
        except (ValueError, AttributeError):
            limit = compatible.generation_limit(None, response.status_code)
        if limit:
            fail(limit, 'Материал не поместился в контекст выбранной модели.')
    if response.status_code != 200:
        fail('OPENAI_UNAVAILABLE', 'OpenAI API не выполнил запрос. Проверьте доступность сервиса и параметры модели.')
    try:
        result = response.json()
        if result.get('status') != 'completed':
            if (result.get('incomplete_details') or {}).get('reason') == 'max_output_tokens':
                fail('OUTPUT_LENGTH', 'Модель достигла своего лимита ответа до конца конспекта.')
            if compatible.generation_limit(result.get('error')):
                fail('INPUT_LENGTH', 'Материал не поместился в контекст выбранной модели.')
            fail('OPENAI_INCOMPLETE', 'OpenAI не завершил ответ. Попробуйте сократить материал или выбрать другой объём.')
        parts = []
        for item in result['output']:
            if item['type'] == 'message' and item.get('role') == 'assistant' and item.get('phase') in (None, 'final_answer'):
                for part in item['content']:
                    if part['type'] == 'refusal':
                        fail('OPENAI_REFUSAL', 'OpenAI отклонил генерацию для этого материала.')
                    if part['type'] == 'output_text':
                        parts.append(part['text'])
        markdown = '\n\n'.join(parts).strip()
    except (ValueError, TypeError, AttributeError, KeyError):
        fail('OPENAI_OUTPUT', 'Не удалось прочитать ответ OpenAI.')
    if not markdown:
        fail('OPENAI_OUTPUT', 'OpenAI вернул пустой конспект.')
    return markdown


def selection_option(body, catalog):
    return next((model for model in catalog if model['id'] == body.model), None)


def openai_snapshot(body):
    with profile_lock(body.userId):
        validate_identity(body)
        if not valid_model(body.model) or body.effort not in EFFORTS or not connected(body.userId, body.provider):
            fail('INVALID_MODEL', 'Проверьте подключение и модель.')
        config = api_config(body.userId)
        if (body.apiBase or OPENAI_BASE) != config['baseUrl']:
            fail('API_CHANGED', 'Адрес API изменился. Создайте задание с новым подключением.')
        catalog = catalog_for(body.userId, body.provider)[0] if body.apiBase or body.slideImages else []
    return config, catalog


def claude_note(body, system, prompt, detailed, images, timeout):
    argv = ['claude', '-p', '--safe-mode', '--restricted', '--tools', '', '--setting-sources', '',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence',
            '--permission-mode', 'dontAsk', '--model', body.model, '--system-prompt', system]
    if images:
        content = [{'type': 'text', 'text': prompt}]
        for image in images:
            content.extend([{'type': 'text', 'text': f'Слайд {image.page}'},
                            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': image.jpeg}}])
        prompt = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content},
                             'parent_tool_use_id': None, 'session_id': ''}, ensure_ascii=False) + '\n'
        argv += ['--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose']
    else:
        argv += ['--output-format', 'json']
    output = cli_run(body.userId, argv, cwd=profile(body.userId) / 'work', detailed=detailed,
                     timeout=timeout or (DETAILED_GENERATION_SECONDS if detailed else 240),
                     output_limit=8_000_000 if detailed else 1_000_000, text_input=prompt, allow_failure=True)
    try:
        if images:
            events = [json.loads(line) for line in output.splitlines()]
            result = next((event for event in reversed(events) if event.get('type') == 'result'), {})
        else:
            result = json.loads(output)
        if result.get('stop_reason') == 'max_tokens' or result.get('terminal_reason') == 'output_limit':
            fail('OUTPUT_LENGTH', 'Модель достигла своего лимита ответа до конца конспекта.')
        if result.get('is_error'):
            message = str(result.get('result') or '')
            if compatible.generation_limit(message):
                fail('INPUT_LENGTH', 'Материал не поместился в контекст Claude.')
            if re.search(r'output token (?:maximum|limit)', message[:2000], re.IGNORECASE):
                fail('OUTPUT_LENGTH', 'Модель достигла своего лимита ответа до конца конспекта.')
        if result.get('is_error') or not result.get('result'):
            fail('CLAUDE_ERROR', 'Claude не вернул конспект. Проверьте ключ Anthropic API, баланс и доступ к модели.')
        return result['result']
    except (ValueError, AttributeError):
        fail('CLAUDE_OUTPUT', 'Не удалось прочитать ответ Claude.')


def codex_note(body, option, system, prompt, detailed, images, timeout):
    with tempfile.TemporaryDirectory(dir=profile(body.userId) / 'work') as directory:
        work = Path(directory)
        schema = work / 'output-schema.json'
        schema.write_text(json.dumps({'type': 'object', 'properties': {'markdown': {'type': 'string'}},
                                      'required': ['markdown'], 'additionalProperties': False}))
        restricted_catalog = work / 'catalog.json'
        restricted_catalog.write_text(json.dumps({'models': [codex_specs[body.userId][body.model]]}))
        argv = ['codex', '-a', 'never', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                '--skip-git-repo-check', '--sandbox', 'read-only', '--model', body.model, '-c', 'project_doc_max_bytes=0',
                '-c', 'web_search="disabled"', '-c', 'tools.update_plan.enabled=false',
                '-c', 'tools.experimental_request_user_input.enabled=false', '-c', f'model_catalog_json="{restricted_catalog}"']
        if option['efforts']:
            argv += ['-c', f'model_reasoning_effort="{body.effort}"']
        for feature in ['shell_tool', 'unified_exec', 'view_image', 'image_generation', 'apps', 'browser_use', 'computer_use',
                        'multi_agent', 'multi_agent_v2', 'skill_search', 'deferred_executor', 'request_permissions_tool',
                        'token_budget', 'current_time_reminder', 'sleep_tool', 'tool_suggest', 'send_message_to_user_async',
                        'memory_tool', 'search_tool', 'web_search', 'tool_search']:
            argv += ['-c', f'features.{feature}=false']
        for image in images:
            path = work / f'slide-{image.page}.jpg'
            path.write_bytes(base64.b64decode(image.jpeg))
            argv += ['--image', str(path)]
        argv += ['-c', 'features.skip_host_skill_discovery=true', '--json', '--output-schema', str(schema), '-']
        output = cli_run(body.userId, argv, cwd=directory,
                         timeout=timeout or (DETAILED_GENERATION_SECONDS if detailed else 300),
                         output_limit=8_000_000 if detailed else 1_000_000,
                         text_input=system + '\n\n' + prompt, allow_failure=True)
    final_answer = ''
    failed = False
    try:
        for line in output.splitlines():
            event = json.loads(line)
            item = event.get('item', {})
            if item.get('type') in ['command_execution', 'mcp_tool_call', 'file_change']:
                fail('UNEXPECTED_TOOL', 'Codex попытался вызвать инструмент; результат не принят.')
            event_type = event.get('type')
            if event_type in ('error', 'turn.failed'):
                failed = failed or event_type == 'turn.failed'
                error = event.get('error') or event
                if compatible.generation_limit(error):
                    fail('INPUT_LENGTH', 'Материал не поместился в контекст Codex.')
                message = str(error.get('message', '') if isinstance(error, dict) else error)
                if re.search(r'max(?:imum)?[_ ]output[_ ]tokens|output.{0,40}token.{0,40}limit', message, re.IGNORECASE):
                    fail('OUTPUT_LENGTH', 'Модель достигла своего лимита ответа до конца конспекта.')
            if event_type == 'item.completed' and item.get('type') == 'agent_message':
                final_answer = item['text']
        markdown = json.loads(final_answer).get('markdown', '') if final_answer and not failed else ''
    except (ValueError, KeyError, AttributeError):
        fail('CODEX_OUTPUT', 'Не удалось прочитать ответ Codex. Проверьте доступность выбранной модели.')
    if not markdown:
        fail('CODEX_ERROR', 'Codex не вернул конспект. Проверьте авторизацию и лимиты выбранной модели.')
    return markdown


def call_model(body, config, option, system, prompt, *, detailed=False, images=(), timeout=None):
    if images and not option.get('supportsImages'):
        fail('IMAGE_MODEL_REQUIRED', 'Выбранная модель не поддерживает анализ слайдов.')
    if body.provider == 'claude':
        return claude_note(body, system, prompt, detailed, images, timeout)
    if body.provider == 'codex':
        return codex_note(body, option, system, prompt, detailed, images, timeout)
    options = {'detailed': detailed}
    if images:
        options['images'] = images
    if timeout is not None:
        options['timeout'] = timeout
    effort = body.effort if body.effort in option.get('efforts', ()) else None
    base = config['baseUrl']
    if body.apiBase is None or (base == OPENAI_BASE and (images or compatible.supports_images({'id': body.model}, base))):
        return openai_response(config['key'], body.model, body.effort if body.apiBase is None else effort,
                               system, prompt, **options)
    if base == ANTHROPIC_BASE and option.get('outputTokens'):
        options['output_tokens'] = option['outputTokens']
    return compatible.generate(config, body.model, system, prompt, effort, **options)


def generation_context(body, snapshot):
    validate_identity(body)
    if body.effort not in EFFORTS or (body.model and not valid_model(body.model)):
        fail('INVALID_MODEL', 'Выберите доступную модель и уровень рассуждения.')
    if snapshot is None and not connected(body.userId, body.provider):
        fail('PROVIDER_AUTH_REQUIRED', 'Войдите в выбранный аккаунт через «Подключения», затем повторите задание.')
    config = None
    if body.provider == 'openai':
        if snapshot is not None:
            config, catalog = snapshot
        else:
            config = api_config(body.userId)
            if (body.apiBase or OPENAI_BASE) != config['baseUrl']:
                fail('API_CHANGED', 'Адрес API изменился. Создайте задание с новым подключением.')
            catalog = catalog_for(body.userId, body.provider)[0] if body.apiBase or body.slideImages or not body.model else []
    else:
        catalog = catalog_for(body.userId, body.provider)[0]
    if not body.model:  # Queued jobs created before model selection was persisted.
        if not catalog:
            fail('INVALID_MODEL', 'Не удалось выбрать модель для старого задания. Создайте задание с выбранной моделью.')
        body.model = catalog[0]['id']
        levels = catalog[0]['efforts']
        if levels and body.effort not in levels:
            body.effort = levels[0]
    option = selection_option(body, catalog)
    if body.provider == 'codex' and (not option or (option['efforts'] and body.effort not in option['efforts'])):
        fail('INVALID_MODEL', 'Выберите доступную модель Codex и уровень рассуждения.')
    return config, option or {}


@router.post('/generate')
def generate(body: GenerateInput):
    if body.provider == 'openai' and body.model:
        return generate_note(body, openai_snapshot(body))
    return generate_serialized(body)


@serialized
def generate_serialized(body: GenerateInput):
    return generate_note(body)


SLIDE_LABEL = re.compile(r'^\[Слайд ([1-9]\d{0,2})\]', re.MULTILINE)
SECTION_BREAK = re.compile(r'\n\s*\n|\n|(?<=[.!?])\s+')
OUTLINE_HEADING = re.compile(r'^ {0,3}#{2,3}[ \t]+([^\r\n]{1,160})', re.MULTILINE)


def group_slides(text):
    # Attach visual descriptions to the same pages before splitting the source into sections.
    labels = list(SLIDE_LABEL.finditer(text))
    if not labels or labels[0].start() or len({label[1] for label in labels}) == len(labels):
        return text
    pages = {}
    for index, label in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
        pages.setdefault(label[1], []).append(text[label.end():end].strip())
    return '\n\n'.join(f'[Слайд {page}]\n' + '\n\n'.join(filter(None, pages[page])) for page in sorted(pages, key=int))


def split_text(text, *, slides=False):
    if not text:
        return '', ''
    middle = len(text) // 2
    start, end = len(text) // 4, len(text) * 3 // 4
    page_breaks = [m.start() for m in SLIDE_LABEL.finditer(text, start, end)] if slides else []
    breaks = page_breaks or [m.end() for m in SECTION_BREAK.finditer(text, start, end)]
    point = min(breaks, key=lambda value: abs(value - middle)) if breaks else middle
    before, after = text[:point], text[point:]
    if slides and not page_breaks:
        preceding = list(SLIDE_LABEL.finditer(before))
        if preceding and not SLIDE_LABEL.match(after):
            after = preceding[-1].group().strip() + '\n' + after
    return before.strip(), after.strip()


@dataclass
class NoteSection:
    transcript: str
    slides: str
    images: tuple

    def size(self):
        return len(self.transcript) + len(self.slides)

    def split(self):
        transcript = split_text(self.transcript)
        slides = split_text(self.slides, slides=True)
        middle = (len(self.images) + 1) // 2
        images = (self.images[:middle], self.images[middle:])
        parts = [NoteSection(*part) for part in zip(transcript, slides, images)]
        return [part for part in parts if part.size() or part.images]


def generate_note(body: GenerateInput, snapshot=None):
    config, option = generation_context(body, snapshot)
    if any(body.pageCount and image.page > body.pageCount for image in body.slideImages):
        fail('INVALID_SLIDE', 'Номер изображения выходит за пределы презентации.')
    detailed = body.length == 'detailed' and body.targetWords is None
    images = tuple(body.slideImages)
    source = NoteSection(body.transcript, group_slides(body.slides), images)
    note_size = min(50_000, round(source.size() * .95))
    deadline = time.monotonic() + (DETAILED_GENERATION_SECONDS if detailed else 300)
    sections = []
    previous_outline = []
    length = 0

    def render(part, is_section=False):
        nonlocal length
        first = not sections
        words = (max(1, round(body.targetWords * (part.size() or len(part.images)) / max(1, source.size() or len(images))))
                 if is_section and body.targetWords else None)
        characters = round(note_size * part.size() / max(1, source.size())) if detailed and note_size >= 8_000 else None
        system = note_instructions(body, words=words, characters=characters, section=is_section, first=first)
        data = {'title': body.title, 'format': 'семинар с задачами' if body.preset == 'seminar' else 'лекция',
                'preferences': body.instructions, 'transcript': part.transcript, 'slides': part.slides}
        if previous_outline:
            data['previousOutline'] = previous_outline
        if part.images:
            data['imagePages'] = [image.page for image in part.images]
        prompt = json.dumps(data, ensure_ascii=False)
        remaining = deadline - time.monotonic()
        if remaining < 1:
            fail('TIMEOUT', 'Превышено время подготовки конспекта.')
        # The estimate is used only with a reported context size. Provider-side limits still govern each call.
        context = option.get('contextTokens')
        estimated_tokens = (len(system) + len(prompt)) / 2 + len(part.images) * 1200
        should_split = context and estimated_tokens > context * .8
        can_split = part.size() > 1200 or len(part.images) > 1
        if can_split and (should_split or (detailed and part.size() > 42_000)):
            for child in part.split():
                render(child, True)
            return
        try:
            timeout = min(remaining, 210) if is_section and not detailed else (remaining if is_section else None)
            markdown = call_model(body, config, option, system, prompt, detailed=detailed, images=part.images, timeout=timeout)
        except HTTPException as error:
            code = error.detail.get('code') if isinstance(error.detail, dict) else None
            if not can_split or (code != 'INPUT_LENGTH' and (code != 'OUTPUT_LENGTH' or not detailed)):
                raise
            for child in part.split():
                render(child, True)
            return
        if not isinstance(markdown, str) or not markdown.strip():
            fail('OUTPUT_LIMIT', 'Провайдер не вернул текст конспекта.')
        markdown = markdown.strip()
        if not first:
            # Keep the document's title even if a model emits another level-one heading.
            markdown = re.sub(r'\A# (?=\S)', '## ', markdown)
        length += len(markdown) + (2 if sections else 0)
        if length > MAX_NOTE_CHARS:
            fail('OUTPUT_LIMIT', 'Ответ провайдера превышает допустимый размер.')
        sections.append(markdown)
        previous_outline.extend(OUTLINE_HEADING.findall(markdown))
        del previous_outline[:-24]

    render(source)
    return {'markdown': '\n\n'.join(sections), 'provider': body.provider, 'model': body.model}


def describe_batch(body, config, option, images, deadline):
    labels = [image.page for image in images]
    prompt = json.dumps({'imagePages': labels}, ensure_ascii=False)
    remaining = deadline - time.monotonic()
    if remaining < 1:
        fail('TIMEOUT', 'Превышено время анализа изображений.')
    try:
        result = call_model(body, config, option, SLIDE_DESCRIPTION, prompt, images=images, timeout=remaining)
        result = re.sub(r'\A\x60{3}(?:json)?\s*|\s*\x60{3}\Z', '', result.strip())
        descriptions = json.loads(result)
        if set(descriptions) != {str(page) for page in labels} or any(
                not isinstance(item, str) or not item.strip() or len(item) > 1200 for item in descriptions.values()):
            raise ValueError()
        return [{'page': page, 'text': descriptions[str(page)].strip()} for page in labels]
    except (ValueError, AttributeError, TypeError):
        if len(images) == 1:
            fail('SLIDE_DESCRIPTION', 'Модель не описала все переданные изображения.')
    except HTTPException as error:
        code = error.detail.get('code') if isinstance(error.detail, dict) else None
        if len(images) == 1 or code not in ('INPUT_LENGTH', 'OUTPUT_LENGTH'):
            raise
    middle = len(images) // 2
    return describe_batch(body, config, option, images[:middle], deadline) + describe_batch(
        body, config, option, images[middle:], deadline)


def describe_slides_with_model(body, snapshot=None):
    if not body.slideImages:
        fail('INVALID_SLIDE', 'Добавьте изображения страниц презентации.')
    config, option = generation_context(body, snapshot)
    pages = describe_batch(body, config, option, body.slideImages, time.monotonic() + 210)
    slides = '\n\n'.join(f"[Слайд {item['page']}]\n{item['text']}" for item in pages)
    return {'slides': slides, 'pages': pages}


@router.post('/describe-slides')
def describe_slides(body: GenerationSelection):
    if body.provider == 'openai':
        return describe_slides_with_model(body, openai_snapshot(body))
    return describe_slides_serialized(body)


@serialized
def describe_slides_serialized(body: GenerationSelection):
    return describe_slides_with_model(body)
