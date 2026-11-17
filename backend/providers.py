"""Server-owned model/effort allowlist. Never accept arbitrary CLI flags."""
CATALOG = [
    {'id': 'codex', 'name': 'Codex', 'models': [
        {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1 Sol', 'efforts': ['low', 'medium', 'high', 'xhigh']},
        {'id': 'gpt-6.1-terra', 'name': 'GPT-6.1 Terra', 'efforts': ['low', 'medium', 'high', 'xhigh']},
        {'id': 'gpt-6-luna', 'name': 'GPT-6 Luna', 'efforts': ['low', 'medium', 'high']},
    ]},
    {'id': 'claude', 'name': 'Claude', 'models': [
        {'id': 'sonnet', 'name': 'Sonnet', 'efforts': ['low', 'medium', 'high']},
        {'id': 'opus', 'name': 'Opus', 'efforts': ['low', 'medium', 'high']},
    ]},
]


def valid_options(provider, model, effort):
    return any(p['id'] == provider and any(m['id'] == model and effort in m['efforts'] for m in p['models']) for p in CATALOG)
