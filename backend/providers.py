"""Server-owned provider, credential and model options."""
import re
from typing import Literal

ProviderId = Literal['codex', 'claude', 'openai']

CATALOG = [
    {'id': 'codex', 'name': 'Codex', 'authType': 'deviceCode', 'models': [
        {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1 Sol', 'efforts': ['low', 'medium', 'high', 'xhigh']},
        {'id': 'gpt-6.1-terra', 'name': 'GPT-6.1 Terra', 'efforts': ['low', 'medium', 'high', 'xhigh']},
        {'id': 'gpt-6-luna', 'name': 'GPT-6 Luna', 'efforts': ['low', 'medium', 'high']},
    ]},
    {'id': 'claude', 'name': 'Claude', 'authType': 'apiKey', 'models': [
        {'id': 'sonnet', 'name': 'Sonnet', 'efforts': ['low', 'medium', 'high']},
        {'id': 'opus', 'name': 'Opus', 'efforts': ['low', 'medium', 'high']},
    ]},
    {'id': 'openai', 'name': 'OpenAI API', 'authType': 'apiKey', 'models': [
        {'id': 'gpt-6.1-terra', 'name': 'GPT-6.1 Terra', 'efforts': ['low', 'medium', 'high']},
        {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1 Sol', 'efforts': ['low', 'medium', 'high']},
    ]},
]
PROVIDERS = {p['id']: p for p in CATALOG}


def valid_api_key(provider, key):
    if not isinstance(key, str) or not 20 <= len(key) <= 512:
        return False
    if provider == 'claude':
        return bool(re.fullmatch(r'sk-ant-[A-Za-z0-9_-]{14,500}', key))
    if provider == 'openai':
        return bool(re.fullmatch(r'sk-(?!ant-)[A-Za-z0-9_-]+', key))
    return False


def valid_options(provider, model, effort):
    return any(p['id'] == provider and any(m['id'] == model and effort in m['efforts'] for m in p['models']) for p in CATALOG)
