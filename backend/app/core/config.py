from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://crm:crm@127.0.0.1:54329/crm'
    environment: str = 'development'
    secret_key: str = 'development-only-change-before-deployment-32-chars'
    storage_dir: Path = Path('.runtime/files')
    max_file_size: int = 25 * 1024 * 1024
    session_hours: int = 12
    # Failed sign-ins are limited per account and, with a higher threshold, per client address,
    # so one person's typos cannot lock out a whole office behind the same proxy or NAT.
    login_window_minutes: int = 15
    login_identity_attempts: int = 10
    login_ip_attempts: int = 50
    allowed_origins: str = 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080'
    company_timezone: str = 'Europe/Moscow'
    clamav_host: str = '127.0.0.1'
    clamav_port: int = 3310
    require_mfa: bool = False
    crm_api_password: str = 'crm_api_password'
    crm_backup_password: str = 'crm_backup_password'
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_user: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    smtp_starttls: bool = True

    def verify(self) -> None:
        if self.environment == 'production':
            if len(self.secret_key) < 32 or self.secret_key.startswith('development'):
                raise RuntimeError('Production SECRET_KEY must be independently generated')
            if not self.database_url.startswith('postgresql'):
                raise RuntimeError('Production requires PostgreSQL')
            self.require_mfa = True


settings = Settings()
settings.verify()
