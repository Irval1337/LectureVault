import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data: Path = Path(os.environ.get('LV_DATA', '/data'))
    jobs: Path = Path(os.environ.get('LV_JOBS', '/jobs'))
    secrets: Path = Path(os.environ.get('LV_SECRETS', '/secrets'))
    media_url: str = os.environ.get('LV_MEDIA_URL', 'http://media:8090')
    provider_alice: str = os.environ.get('LV_PROVIDER_ALICE', 'http://provider-alice:8090')
    provider_bob: str = os.environ.get('LV_PROVIDER_BOB', 'http://provider-bob:8090')
    mock_seconds: float = float(os.environ.get('LV_MOCK_SECONDS', '6'))
    max_video: int = 512 * 1024 * 1024
    max_pdf: int = 50 * 1024 * 1024
    max_image: int = 10 * 1024 * 1024

    def provider_url(self, user_id):
        if user_id not in ['alice', 'bob']:
            raise ValueError('Unknown profile')
        return self.provider_alice if user_id == 'alice' else self.provider_bob


USERS = [{'id': 'alice', 'name': 'Алексей'}, {'id': 'bob', 'name': 'Мария'}]
