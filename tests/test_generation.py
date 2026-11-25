import base64
import json

import httpx

from backend.providers import GEMINI_BASE
from runner import compatible, provider


USER = 'd6ff3c7b-4503-4630-b92f-a0e14d880ccb'
JPEG = base64.b64encode(b'\xff\xd8\xfffixture\xff\xd9').decode()


def test_gemini_refresh_and_generation_reuse_saved_key_and_filter_models(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, 'PROFILES', tmp_path)
    monkeypatch.setattr(provider, 'status_cache', {})
    monkeypatch.setattr(provider, 'model_cache', {})
    monkeypatch.setattr(provider, 'last_public_status', {})
    monkeypatch.setattr(provider, 'connected', lambda identity, name: name == 'openai' and (tmp_path / identity / 'openai-key.enc').exists())
    profile = provider.ApiKey(userId=USER, provider='openai', baseUrl=GEMINI_BASE, apiKey='gemini-key')
    discovery = []
    requests = []

    def request(config, endpoint, payload=None, **kwargs):
        assert config['key'] == profile.apiKey and config['baseUrl'] == GEMINI_BASE
        if endpoint == 'models':
            discovery.append(endpoint)
            return {'data': [{'id': f'gemini-3-flash-preview-{len(discovery)}'},
                             {'id': 'gemini-3-pro-image-preview'}, {'id': 'gemini-embedding-001'},
                             {'id': 'gemini-2.5-flash-preview-tts'}, {'id': 'gemini-2.5-flash-live'},
                             {'id': 'gemini-3.5-transcribe'}, {'id': 'gemini-omni-1.2-flash'},
                             {'id': 'text-only', 'input_modalities': ['text'], 'output_modalities': ['text']}]}
        assert endpoint == 'chat/completions'
        requests.append(payload)
        return {'choices': [{'message': {'content': '# Конспект'}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr(compatible, 'request', request)
    provider.save_key(profile)
    ordinary = provider.provider_status({'userId': USER})['providers'][1]
    refreshed = provider.provider_status({'userId': USER, 'refresh': True})['providers'][1]
    assert [model['id'] for model in ordinary['models']] == ['gemini-3-flash-preview-1', 'text-only']
    assert [(model['id'], model['supportsImages']) for model in refreshed['models']] == [
        ('gemini-3-flash-preview-2', True), ('text-only', False)]
    assert len(discovery) == 2 and profile.apiKey not in str(refreshed)
    body = provider.GenerateInput(userId=USER, provider='openai', apiBase=GEMINI_BASE,
                                  model='gemini-3-flash-preview-2', effort='medium', title='Тема', preset='seminar',
                                  length='detailed', instructions='', transcript='Расшифровка', pageCount=7,
                                  slideImages=[{'page': 7, 'jpeg': JPEG}])
    assert provider.generate(body)['markdown'] == '# Конспект'
    messages = requests[0]['messages']
    assert 'ключевой код' in messages[0]['content'] and '[[slide:N]]' in messages[0]['content']
    assert messages[1]['content'][1:] == [
        {'type': 'text', 'text': 'Слайд 7'}, {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{JPEG}'}}]


def test_detailed_fallback_preserves_late_material_after_provider_output_limit(monkeypatch):
    calls = []
    opening = 'Первая тема. ' * 250
    closing = 'Заключительная тема. ' * 250

    def reply(_url, **kwargs):
        request = kwargs['json']
        calls.append(json.loads(request['input']))
        if len(calls) == 1:
            return httpx.Response(200, json={'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}})
        markdown = '# Лекция\n\n## Начало' if len(calls) == 2 else '## Завершение'
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': markdown}]}]})

    monkeypatch.setattr(httpx, 'post', reply)
    monkeypatch.setattr(provider, 'connected', lambda *_: True)
    monkeypatch.setattr(provider, 'api_config', lambda *_: {'baseUrl': provider.OPENAI_BASE, 'key': 'fixture'})
    body = provider.GenerateInput(userId=USER, provider='openai', model='gpt-5.5', effort='medium', title='Тема',
                                  preset='lecture', length='detailed', instructions='', transcript=opening + '\n\n' + closing)
    assert provider.generate(body)['markdown'] == '# Лекция\n\n## Начало\n\n## Завершение'
    assert calls[0]['transcript'] == body.transcript
    assert ' '.join(part['transcript'] for part in calls[1:]).split() == body.transcript.split()
    assert calls[-1]['transcript'].endswith('Заключительная тема.')
    assert 'previousOutline' not in calls[1]
    assert calls[-1]['previousOutline'] == ['Начало']


def test_long_detailed_job_sends_all_source_in_order_with_proportional_targets(monkeypatch):
    monkeypatch.setattr(provider, 'connected', lambda *_: True)
    monkeypatch.setattr(provider, 'api_config', lambda *_: {'baseUrl': provider.OPENAI_BASE, 'key': 'fixture'})
    calls = []

    def mock_model(_body, _config, _option, instructions, text, **kwargs):
        calls.append((instructions, json.loads(text)))
        assert kwargs['detailed']
        return f'## Разбор темы {len(calls)}\n\nУчебный материал.'

    monkeypatch.setattr(provider, 'call_model', mock_model)
    transcript = '\n\n'.join(f'Тема {i}. ' + 'Доказательство и пример. ' * 270 for i in range(9))
    body = provider.GenerateInput(userId=USER, provider='openai', model='gpt-test', effort='medium',
                                  title='Лекция', preset='lecture', length='detailed', instructions='', transcript=transcript)
    result = provider.generate(body)['markdown']
    assert len(calls) == 2 and result.count('## Разбор темы') == 2
    assert all('символов в этой части' in instructions for instructions, _ in calls)
    assert 'previousOutline' not in calls[0][1]
    assert calls[1][1]['previousOutline'] == ['Разбор темы 1']
    assert 'отличающиеся примеры' in calls[1][0]
    assert 'Тема 8.' in calls[-1][1]['transcript']
    assert ' '.join(part['transcript'] for _, part in calls).split() == transcript.split()


def test_slide_descriptions_validate_page_coverage_and_group_with_extracted_slides(monkeypatch):
    monkeypatch.setattr(provider, 'connected', lambda *_: True)
    monkeypatch.setattr(provider, 'api_config', lambda *_: {'baseUrl': GEMINI_BASE, 'key': 'fixture'})
    monkeypatch.setattr(provider, 'catalog_for', lambda *_: ([{'id': 'gemini-3-flash', 'supportsImages': True, 'efforts': []}], ''))
    batches = []

    def generate(config, model, system, prompt, effort, *, images, **kwargs):
        pages = [image.page for image in images]
        batches.append(pages)
        if len(pages) > 1:
            return '{"2":"Пропущены страницы"}'
        return json.dumps({pages[0]: f'Схема {pages[0]}'})

    monkeypatch.setattr(compatible, 'generate', generate)
    body = provider.GenerationSelection(userId=USER, provider='openai', apiBase=GEMINI_BASE,
                                        model='gemini-3-flash', effort='medium',
                                        slideImages=[{'page': page, 'jpeg': JPEG} for page in [4, 2]])
    result = provider.describe_slides(body)
    assert batches == [[2, 4], [2], [4]]
    assert result['pages'] == [{'page': 2, 'text': 'Схема 2'}, {'page': 4, 'text': 'Схема 4'}]
    assert provider.group_slides('[Слайд 2] Текст\n\n' + result['slides']) == '[Слайд 2]\nТекст\n\nСхема 2\n\n[Слайд 4]\nСхема 4'
