"""Bounded OpenAI-compatible HTTP calls, with the resolved address pinned per request."""
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlsplit

import httpx

from backend.providers import (ANTHROPIC_BASE, DETAILED_GENERATION_SECONDS, DETAILED_REQUIRED_TOKEN_BUDGET,
                               EFFORTS, GEMINI_BASE, OPENAI_BASE, valid_model)
from .common import fail


def local_bases():
    return {v.strip().rstrip('/') for v in os.getenv('LV_LOCAL_MODEL_BASES', '').split(',') if v.strip()}


def normalize(value):
    value = value.strip().rstrip('/')
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError:
        host = None
    if (not host or len(value) > 500 or not re.fullmatch(r'[a-zA-Z0-9.:\[\]-]+', url.netloc)
            or not re.fullmatch(r'(/[a-zA-Z0-9._~-]+)*', url.path) or any(p in ('.', '..') for p in url.path.split('/'))
            or url.query or url.fragment or url.scheme not in ('http', 'https')
            or (value not in local_bases() and (url.scheme != 'https' or port not in (None, 443)))):
        fail('API_URL', 'Укажите HTTPS Base URL. Локальный адрес должен быть разрешён администратором.')
    return value


def generation_limit(error, status=None):
    if status == 413:
        return 'INPUT_LENGTH'
    if isinstance(error, dict):
        code = str(error.get('code') or error.get('type') or '')
        message = str(error.get('message') or '')
    else:
        code, message = '', str(error)
    if code in ('context_length_exceeded', 'context_window_exceeded', 'model_context_window_exceeded', 'prompt_too_long'):
        return 'INPUT_LENGTH'
    if re.search(r'context (?:length|window).{0,60}(?:exceed|too (?:large|long)|limit)|'
                 r'(?:exceed|over).{0,40}context (?:length|window)|'
                 r'ran out of room.{0,40}context window|'
                 r'(?:input|prompt).{0,50}(?:too long|too large|exceeds?|longer than)|'
                 r'too many (?:input )?tokens|maximum number of input tokens',
                 message[:2000], re.IGNORECASE):
        return 'INPUT_LENGTH'
    return None


def request(config, endpoint, payload=None, *, detailed=False, timeout=None):
    base = normalize(config['baseUrl'])
    if endpoint not in ('models', 'chat/completions'):
        raise ValueError('Invalid provider method')
    url = httpx.URL(base + '/' + endpoint)
    try:
        addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(url.host, url.port or (443 if url.scheme == 'https' else 80), type=socket.SOCK_STREAM)]
        if not addresses:
            raise OSError()
        if base not in local_bases() and any(not a.is_global or a.is_multicast or a.is_reserved or
                (a.version == 6 and (a.ipv4_mapped or a.sixtofour or a.teredo or a in ipaddress.ip_network('64:ff9b::/96'))) for a in addresses):
            fail('API_URL', 'Этот адрес недоступен для публичного API.')
        address = next((a for a in addresses if a.version == 4), addresses[0])
        headers = {'Host': url.netloc.decode(), 'Accept-Encoding': 'identity'}
        if config['key'] and (base != ANTHROPIC_BASE or endpoint != 'models'):
            headers['Authorization'] = 'Bearer ' + config['key']
        if base == ANTHROPIC_BASE:
            headers['anthropic-version'] = '2023-06-01'
            if endpoint == 'models':
                headers['x-api-key'] = config['key']
            if config.get('workspace'):
                headers['anthropic-workspace-id'] = config['workspace']
        timeout = timeout or (DETAILED_GENERATION_SECONDS if detailed else (210 if payload else 7))
        output_limit = 8_000_000 if detailed else 2_000_000
        start = time.monotonic()
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(timeout, connect=5)) as client:
            with client.stream('POST' if payload else 'GET', url.copy_with(host=str(address)), headers=headers,
                               extensions={'sni_hostname': url.host}, json=payload) as reply:
                if reply.status_code != 200:
                    if payload and reply.status_code in (400, 413, 422):
                        limit = generation_limit(None, reply.status_code)
                        if not limit and reply.headers.get('content-encoding', 'identity').lower() in ('', 'identity'):
                            raw = next(reply.iter_raw(chunk_size=8192), b'')
                            try:
                                error = json.loads(raw)
                                if isinstance(error, list) and error:
                                    error = error[0]
                            except ValueError:
                                error = raw.decode(errors='replace')
                            limit = generation_limit(error.get('error', error) if isinstance(error, dict) else error)
                        if limit:
                            fail(limit, 'Материал не поместился в контекст выбранной модели.')
                    message = {401: 'Проверьте ключ API.', 403: 'Проверьте доступ ключа к модели.', 404: 'Проверьте Base URL и имя модели.', 429: 'Проверьте квоту и баланс.'}.get(reply.status_code, 'Проверьте совместимость адреса и модели.')
                    code = 'API_MODELS_UNSUPPORTED' if endpoint == 'models' and reply.status_code in (404, 405, 501) else 'API_RESPONSE'
                    fail(code, f'HTTP {reply.status_code}. {message}')
                if reply.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
                    fail('API_RESPONSE', 'API вернул сжатый ответ вопреки запросу.')
                data = bytearray()
                for chunk in reply.iter_raw():
                    if len(data) + len(chunk) > output_limit or time.monotonic() - start > timeout:
                        fail('API_LIMIT', 'Ответ API превысил допустимый размер или время ожидания.')
                    data.extend(chunk)
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (httpx.RequestError, OSError, ValueError):
        fail('API_UNAVAILABLE', 'Не удалось получить ответ API. Проверьте адрес и доступность сервера.')


def modalities(row, direction):
    architecture = row.get('architecture')
    for source in (row, architecture):
        if isinstance(source, dict) and isinstance(source.get(f'{direction}_modalities'), list):
            return source[f'{direction}_modalities']
    return None


def model_for_notes(row, base):
    outputs = modalities(row, 'output')
    if outputs is not None and 'text' not in outputs:
        return False
    inputs = modalities(row, 'input')
    if inputs is not None and 'text' not in inputs:
        return False
    if row.get('type') in ('embedding', 'embeddings', 'rerank', 'tts', 'stt', 'image', 'video', 'audio'):
        return False
    model = row['id'].lower().removeprefix('models/')
    if base == GEMINI_BASE:
        methods = row.get('supportedGenerationMethods')
        if isinstance(methods, list) and 'generateContent' not in methods:
            return False
        return not re.search(r'(?:^|[-_])(?:embed(?:ding|dings)?|imagen|veo|image|video|tts|audio|transcrib(?:e|er)|'
                             r'transcription|live|robotics|'
                             r'computer-use|aqa|deep-research|lyria|nano-banana|omni|genie|antigravity|decision)(?:$|[-_])', model)
    if base == OPENAI_BASE:
        return not model.startswith(('dall-e', 'gpt-image', 'sora', 'whisper', 'tts-', 'text-embedding',
                                     'babbage-', 'davinci-', 'omni-moderation', 'text-moderation')) and not re.search(
                                     r'(?:^|-)(?:audio|transcribe|realtime|tts)(?:-|$)', model)
    return True


def supports_images(row, base):
    inputs = modalities(row, 'input')
    if inputs is not None:
        return 'image' in inputs
    capabilities = row.get('capabilities')
    if isinstance(capabilities, dict) and isinstance(capabilities.get('vision'), bool):
        return capabilities['vision']
    model = row['id'].lower().removeprefix('models/')
    if base == GEMINI_BASE:
        return bool(re.match(r'gemini-(?:[2-9](?:[.-]|$)|1\.5(?:-|$)|(?:flash(?:-lite)?|pro)-latest$)', model))
    if base == ANTHROPIC_BASE:
        return bool(re.match(r'claude-(?:3|(?:haiku|sonnet|opus)-[4-9])(?:[-.]|$)', model))
    if base == OPENAI_BASE:
        return bool(re.match(r'(?:gpt-(?:4o|4\.1|[5-9])(?:[-.]|$)|o(?:1|3|4)(?:-|$))', model)) and model not in (
            'o1-mini', 'o1-preview', 'o3-mini')
    return False


def model_limit(value):
    return value if type(value) is int and value > 0 else None


def models(config):
    rows = request(config, 'models').get('data')
    if not isinstance(rows, list):
        fail('API_MODELS', 'Сервис не вернул каталог моделей.')
    entries = {}
    for row in rows:
        if not isinstance(row, dict) or not valid_model(row.get('id')):
            continue
        if not model_for_notes(row, config['baseUrl']):
            continue
        model = row['id']
        levels = row.get('supported_reasoning_levels')
        efforts = []
        if isinstance(levels, list) and config['baseUrl'] != ANTHROPIC_BASE:
            reported = [level.get('effort') if isinstance(level, dict) else level for level in levels]
            efforts = [level for level in EFFORTS if level in reported]
        entry = {'id': model, 'name': str(row.get('display_name') or row.get('name') or model)[:180],
                 'efforts': efforts, 'supportsImages': supports_images(row, config['baseUrl'])}
        context = model_limit(row.get('max_input_tokens') or row.get('context_length') or row.get('inputTokenLimit'))
        output = model_limit(row.get('max_tokens') or row.get('max_output_tokens') or row.get('outputTokenLimit'))
        if context:
            entry['contextTokens'] = context
        if output:
            entry['outputTokens'] = output
        entries[model] = entry
    if not entries:
        fail('API_EMPTY_CATALOG', 'Сервер не вернул доступных моделей. Проверьте доступ или загрузите модель на сервер.')
    return list(entries.values())


def generate(config, model, system, prompt, effort=None, *, detailed=False, images=(), output_tokens=None, timeout=None):
    content = prompt
    if images:
        content = [{'type': 'text', 'text': prompt}]
        for image in images:
            content.extend([{'type': 'text', 'text': f'Слайд {image.page}'},
                            {'type': 'image_url', 'image_url': {'url': image.data_url}}])
    payload = {'model': model, 'messages': [
        {'role': 'system', 'content': system}, {'role': 'user', 'content': content}]}
    if config['baseUrl'] == ANTHROPIC_BASE:
        budget = DETAILED_REQUIRED_TOKEN_BUDGET if detailed else 8192
        payload['max_tokens'] = min(budget, output_tokens) if output_tokens else budget
    elif effort:
        payload['reasoning_effort'] = effort
    options = {'detailed': True} if detailed else {}
    if timeout is not None:
        options['timeout'] = timeout
    reply = request(config, 'chat/completions', payload, **options)
    try:
        choice = reply['choices'][0]
        if choice.get('finish_reason') in ('length', 'max_tokens', 'MAX_TOKENS'):
            fail('OUTPUT_LENGTH', 'Модель достигла своего лимита ответа до конца конспекта.')
        text = choice['message']['content']
        if choice.get('finish_reason') not in ('stop', None) or not isinstance(text, str) or not text.strip():
            raise ValueError()
        return text.strip()
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        fail('API_OUTPUT', 'Модель вернула пустой или незавершённый текст.')
