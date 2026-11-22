"""Public descriptors. Model availability is discovered by the isolated broker."""
import re
from typing import Literal

ProviderId = Literal['codex', 'claude', 'openai']
OPENAI_BASE = 'https://api.openai.com/v1'
ANTHROPIC_BASE = 'https://api.anthropic.com/v1'
CATALOG = [
    {'id': 'codex', 'name': 'Codex', 'authType': 'deviceCode', 'models': []},
    {'id': 'openai', 'name': 'Свой API', 'authType': 'apiKey', 'models': []},
    {'id': 'claude', 'name': 'Claude Code', 'authType': 'apiKey', 'models': []},
]
PROVIDERS = {p['id']: p for p in CATALOG}
EFFORTS = ('low', 'medium', 'high', 'xhigh')
def valid_api_key(provider, key):
    if not isinstance(key, str) or len(key) > 512:
        return False
    if provider == 'claude':
        return bool(re.fullmatch(r'sk-ant-[A-Za-z0-9_-]{14,500}', key))
    return provider == 'openai' and all(32 < ord(ch) < 127 for ch in key)

def valid_model(model):
    return isinstance(model, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,179}', model))

def valid_options(provider, model, effort):
    return provider in PROVIDERS and valid_model(model) and effort in EFFORTS
