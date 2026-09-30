"""환경변수 기반 설정. 시크릿은 .env에만 두고 코드·레포에는 두지 않는다 (CLAUDE.md 3절 #1).

컨테이너마다 필요한 시크릿만 넘긴다 — 마이그레이션 컨테이너에는 JWT 키를,
API 컨테이너에는 시드 비밀번호를 주지 않는다. 그래서 둘 다 선택 필드이고,
실제로 쓰는 쪽(create_app, seed)에서 존재·길이를 검사한다.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLATFORM_")

    db_host: str = "platform-db"
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: SecretStr

    # 관리자 로그인 토큰(JWT HS256) 서명 키 — platform-api에만
    auth_secret: SecretStr | None = None
    # 마지막 요청 뒤 이 시간이 지나면 다시 로그인 (요청마다 연장)
    session_idle_minutes: int = 30
    # Secure 쿠키(HTTPS에서만 전송). 브라우저는 http://localhost를 예외로 허용한다
    cookie_secure: bool = True

    # 시드 취급자 계정의 비밀번호 — platform-seed에만
    seed_operator_password: SecretStr | None = None

    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    def require_auth_secret(self) -> bytes:
        secret = self.auth_secret.get_secret_value() if self.auth_secret else ""
        if len(secret) < MIN_SECRET_LENGTH:
            # 짧은 HMAC 키는 오프라인 대입으로 토큰 위조가 가능해진다
            raise ValueError(f"PLATFORM_AUTH_SECRET must be at least {MIN_SECRET_LENGTH} chars")
        return secret.encode()
