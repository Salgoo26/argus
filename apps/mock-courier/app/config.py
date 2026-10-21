"""환경변수 기반 설정 (접두사 COURIER_). 키는 .env에만 둔다.

- api_key: 쇼핑몰 서버(platform-api)가 접수·조회 API를 부를 때 쓰는 키
- webhook_secret: 이 서비스가 쇼핑몰에 상태를 통지할 때 서명하는 키 (쇼핑몰이 검증)
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COURIER_")

    db_host: str = "mock-courier-db"
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: SecretStr

    api_key: SecretStr
    webhook_url: str = "http://platform-api:8000/partners/courier/events"
    webhook_secret: SecretStr

    company_name: str = "데모택배(가상)"
    # 상태 진행·통지 루프 간격(초). 테스트는 루프를 끄고 tick()을 직접 부른다
    tick_seconds: float = 2.0
    worker_enabled: bool = True

    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    def require_keys(self) -> tuple[str, bytes]:
        api_key = self.api_key.get_secret_value()
        secret = self.webhook_secret.get_secret_value()
        if len(api_key) < MIN_SECRET_LENGTH or len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                "COURIER_API_KEY and COURIER_WEBHOOK_SECRET must be at least"
                f" {MIN_SECRET_LENGTH} chars"
            )
        if not self.webhook_url.startswith(("http://", "https://")):
            raise ValueError("COURIER_WEBHOOK_URL must be an http(s) URL")
        return api_key, secret.encode()
