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

    # Argus 사용자(담당자 A4·취급자 A5) 로그인 토큰(JWT HS256) 서명 키 — argus-api에만.
    # 인증 방식은 플랫폼 관리자와 같다 (policy 4-3)
    auth_secret: SecretStr | None = None
    session_idle_minutes: int = 30
    # Secure 쿠키(HTTPS에서만 전송). 브라우저는 http://localhost를 예외로 허용한다
    cookie_secure: bool = True
    # 접속 IP 헤더(X-Forwarded-For)를 믿을 프록시 — 쉼표 구분 IP·CIDR, 비우면 아무도 믿지 않는다
    trusted_proxies: str = ""

    # 소명 첨부 파일을 두는 디렉터리 — Argus 전용 볼륨 (기능 레이어 7 ③)
    attachment_dir: str = "/data/attachments"

    # 플랫폼 관리자 화면 주소 — 소명의 관련 티켓에서 플랫폼 문의 상세로 넘어가는 링크용.
    # 서버끼리 통신하지 않는다(브라우저가 이동). 운영에서는 실제 도메인으로
    platform_admin_url: str = "http://localhost:3000/admin"

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

    def require_auth_secret(self) -> bytes:
        secret = self.auth_secret.get_secret_value() if self.auth_secret else ""
        if len(secret) < 32:
            # 짧은 HMAC 키는 오프라인 대입으로 토큰 위조가 가능해진다
            raise ValueError("ARGUS_AUTH_SECRET must be at least 32 chars")
        return secret.encode()

    def ingest_secret(self, source_code: str) -> bytes | None:
        secrets = {"PLATFORM": self.ingest_secret_platform}
        secret = secrets.get(source_code)
        if secret is None or not secret.get_secret_value():
            return None
        return secret.get_secret_value().encode()

    def require_platform_admin_url(self) -> str:
        url = self.platform_admin_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            # 화면에 링크로 나간다 — javascript: 같은 스킴이 설정 실수로 들어가지 않게
            raise ValueError("ARGUS_PLATFORM_ADMIN_URL must be an http(s) URL")
        return url
