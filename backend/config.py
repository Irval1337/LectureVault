import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def flag(name, default=False):
    return os.environ.get(name, str(int(default))).lower() in {'1', 'true', 'yes'}


@dataclass
class Settings:
    data: Path = Path(os.environ.get('LV_DATA', '/data'))
    jobs: Path = Path(os.environ.get('LV_JOBS', '/jobs'))
    secrets: Path = Path(os.environ.get('LV_SECRETS', '/secrets'))
    environment: str = os.environ.get('LV_ENV', 'development')
    public_url: str = os.environ.get('LV_PUBLIC_URL', 'http://localhost:5180').rstrip('/')
    support_email: str = os.environ.get('LV_SUPPORT_EMAIL', '')
    verify_email: bool = flag('LV_VERIFY_EMAIL', True)
    media_url: str = os.environ.get('LV_MEDIA_URL', 'http://media:8090')
    providers_url: str = os.environ.get('LV_PROVIDERS_URL', 'http://providers:8090')
    smtp_host: str = os.environ.get('LV_SMTP_HOST', 'mailpit')
    smtp_port: int = int(os.environ.get('LV_SMTP_PORT', '1025'))
    smtp_tls: str = os.environ.get('LV_SMTP_TLS', 'none')
    smtp_user: str = os.environ.get('LV_SMTP_USER', '')
    smtp_password_file: str = os.environ.get('LV_SMTP_PASSWORD_FILE', '')
    mail_from: str = os.environ.get('LV_MAIL_FROM', 'LectureVault <noreply@localhost>')
    mock_seconds: float = float(os.environ.get('LV_MOCK_SECONDS', '6'))
    max_video: int = 512 * 1024 * 1024
    max_pdf: int = 50 * 1024 * 1024
    max_image: int = 10 * 1024 * 1024
    user_quota: int = 1024 * 1024 * 1024
    total_quota: int = 10 * 1024 * 1024 * 1024

    def __post_init__(self):
        parsed = urlparse(self.public_url)
        if (self.environment not in {'development', 'test', 'production'} or parsed.scheme not in {'http', 'https'}
                or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Configure LV_ENV and a canonical LV_PUBLIC_URL origin')
        if self.production:
            if parsed.scheme != 'https' or parsed.hostname in {'localhost', '127.0.0.1'}:
                raise ValueError('Production requires a public HTTPS LV_PUBLIC_URL')
            from email.utils import parseaddr
            from email_validator import validate_email
            try:
                validate_email(parseaddr(self.mail_from)[1], check_deliverability=False)
                validate_email(self.support_email, check_deliverability=False)
            except ValueError:
                raise ValueError('Production requires valid LV_MAIL_FROM and LV_SUPPORT_EMAIL') from None
            if not self.verify_email or not self.smtp_host or self.smtp_tls not in {'starttls', 'ssl'} or '@localhost' in self.mail_from:
                raise ValueError('Production requires verified email and TLS SMTP configuration')
        if self.smtp_tls not in {'none', 'starttls', 'ssl'}:
            raise ValueError('Invalid SMTP TLS mode')

    @property
    def production(self):
        return self.environment == 'production'

    @property
    def cookie_name(self):
        return '__Host-lv_session' if self.production else 'lv_session'

    @property
    def origins(self):
        if self.production:
            return {self.public_url}
        return {self.public_url, *(f'http://{host}:{port}' for host in ['localhost', '127.0.0.1'] for port in [5173, 4173, 5180])}

    def provider_url(self, _user_id):
        return self.providers_url
