"""환경변수 기반 설정 (접두사 MOCKPG_). 키는 .env에만 둔다.

- client_key: 결제창을 여는 쪽(쇼핑몰 화면)이 쓰는 공개 키
- secret_key: 승인·조회·취소 API용 비밀 키 — 쇼핑몰 서버(platform-api)에만
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOCKPG_")

    db_host: str = "mock-pg-db"
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: SecretStr

    client_key: str
    secret_key: SecretStr

    # 결제 후 돌아갈 주소로 허용하는 출처(쉼표 구분)
    # — 아무 주소로나 돌려보내는 열린 리다이렉트를 막는다
    allowed_return_origins: str = "http://localhost:3000"
    # 이 서비스를 브라우저가 부르는 주소 — 영수증 링크에 쓴다
    public_url: str = "http://localhost:18090"
    # 결제창을 연 뒤 승인까지 기다리는 시간. 지나면 만료(EXPIRED)
    payment_ttl_minutes: int = 10

    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    def require_secret_key(self) -> str:
        key = self.secret_key.get_secret_value()
        if len(key) < MIN_SECRET_LENGTH:
            raise ValueError(f"MOCKPG_SECRET_KEY must be at least {MIN_SECRET_LENGTH} chars")
        return key

    def return_origins(self) -> frozenset[str]:
        return frozenset(o.strip().rstrip("/") for o in self.allowed_return_origins.split(",") if o)
