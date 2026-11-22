"""Bounded OpenAI-compatible HTTP calls, with the resolved address pinned per request."""
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlsplit

import httpx

from backend.providers import ANTHROPIC_BASE, EFFORTS, valid_model
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


def request(config, endpoint, payload=None):
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
        if config['key']:
            headers['Authorization'] = 'Bearer ' + config['key']
        if base == ANTHROPIC_BASE:
            headers['anthropic-version'] = '2023-06-01'
            if endpoint == 'models':
                headers['x-api-key'] = config['key']
            if config.get('workspace'):
                headers['anthropic-workspace-id'] = config['workspace']
        timeout = 210 if payload else 7
        start = time.monotonic()
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(timeout, connect=5)) as client:
            with client.stream('POST' if payload else 'GET', url.copy_with(host=str(address)), headers=headers,
                               extensions={'sni_hostname': url.host}, json=payload) as reply:
                if reply.status_code != 200:
                    message = {401: 'Проверьте ключ API.', 403: 'Проверьте доступ ключа к модели.', 404: 'Проверьте Base URL и имя модели.', 429: 'Проверьте квоту и баланс.'}.get(reply.status_code, 'Проверьте совместимость адреса и модели.')
                    code = 'API_MODELS_UNSUPPORTED' if endpoint == 'models' and reply.status_code in (404, 405, 501) else 'API_RESPONSE'
                    fail(code, f'HTTP {reply.status_code}. {message}')
                if reply.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
                    fail('API_RESPONSE', 'API вернул сжатый ответ вопреки запросу.')
                data = bytearray()
                for chunk in reply.iter_raw():
                    if len(data) + len(chunk) > 2_000_000 or time.monotonic() - start > timeout:
                        fail('API_LIMIT', 'Ответ API превысил допустимый размер или время ожидания.')
                    data.extend(chunk)
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (httpx.RequestError, OSError, ValueError):
        fail('API_UNAVAILABLE', 'Не удалось получить ответ API. Проверьте адрес и доступность сервера.')


def models(config):
    rows = request(config, 'models').get('data')
    if not isinstance(rows, list):
        fail('API_MODELS', 'Сервис не вернул каталог моделей.')
    entries = {}
    for row in rows:
        if not isinstance(row, dict) or not valid_model(row.get('id')):
            continue
        architecture = row.get('architecture')
        outputs = architecture.get('output_modalities') if isinstance(architecture, dict) else None
        if isinstance(outputs, list) and 'text' not in outputs:
            continue
        if row.get('type') in ('embedding', 'embeddings', 'rerank', 'tts', 'stt'):
            continue
        model = row['id']
        levels = row.get('supported_reasoning_levels')
        efforts = []
        if isinstance(levels, list) and config['baseUrl'] != ANTHROPIC_BASE:
            reported = [level.get('effort') if isinstance(level, dict) else level for level in levels]
            efforts = [level for level in EFFORTS if level in reported]
        entries[model] = {'id': model, 'name': str(row.get('display_name') or row.get('name') or model)[:180], 'efforts': efforts}
    if not entries:
        fail('API_EMPTY_CATALOG', 'Сервер не вернул доступных моделей. Проверьте доступ или загрузите модель на сервер.')
    return list(entries.values())


def generate(config, model, system, prompt, effort=None):
    payload = {'model': model, 'messages': [
        {'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]}
    if config['baseUrl'] == ANTHROPIC_BASE:
        payload['max_tokens'] = 8192
    elif effort:
        payload['reasoning_effort'] = effort
    reply = request(config, 'chat/completions', payload)
    try:
        choice = reply['choices'][0]
        text = choice['message']['content']
        if choice.get('finish_reason') not in ('stop', None) or not isinstance(text, str) or not text.strip():
            raise ValueError()
        return text.strip()
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        fail('API_OUTPUT', 'Модель вернула пустой или незавершённый текст.')
