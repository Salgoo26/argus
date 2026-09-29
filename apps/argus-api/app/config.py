"""환경변수 기반 설정. 시크릿은 .env에만 두고 코드·레포에는 두지 않는다 (CLAUDE.md 3절 #1)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARGUS_")

    # DB 접속 정보. API는 테이블 소유자가 아닌 앱 계정(argus_app 멤버)으로,
    # 마이그레이션은 소유자 계정으로 접속한다 — 같은 필드에 다른 값을 넣어 실행한다.
    db_host: str = "argus-db"
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: SecretStr

    # 출처 시스템별 HMAC 공유 비밀키 (api-spec 1-2). Skeleton은 PLATFORM 하나, 키 1개.
    ingest_secret_platform: SecretStr | None = None

    def database_url(self) -> URL:
        # URL.create는 비밀번호의 특수문자를 알아서 이스케이프한다
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    def ingest_secret(self, source_code: str) -> bytes | None:
        secrets = {"PLATFORM": self.ingest_secret_platform}
        secret = secrets.get(source_code)
        if secret is None or not secret.get_secret_value():
            return None
        return secret.get_secret_value().encode()
