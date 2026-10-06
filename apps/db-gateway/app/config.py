"""환경변수 기반 설정 (CLAUDE.md 3절 #1 — 시크릿은 .env에만)

게이트웨이가 갖는 시크릿 (architecture 7-2 "2티어 시크릿", 컨테이너별 최소 시크릿):
- 플랫폼 DB 공용 계정 비밀번호 — 사람에게 공유하지 않는다
- DB 접속 토큰 서명 키(DB_GATEWAY_TOKEN_KEY) — platform-api와 같은 값
- Argus 수집 API HMAC 키(출처 PLATFORM) — platform-relay와 같은 값
- TLS 키 — 게이트웨이에만 (로컬은 처음 기동할 때 자체 서명으로 만든다)
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    listen_host: str = "0.0.0.0"  # noqa: S104 — 컨테이너 안. 호스트 공개 범위는 compose 포트 설정이 정한다
    listen_port: int = 6432

    # 중계 대상 = 플랫폼 DB. 공용 계정(기존 앱 계정)으로 접속한다
    upstream_host: str = "platform-db"
    upstream_port: int = 5432
    upstream_db: str
    upstream_user: str
    upstream_password: SecretStr

    token_key: SecretStr | None = None

    argus_ingest_url: str = "http://argus-api:8000"
    argus_ingest_secret: SecretStr | None = None

    # 원문 저장소·전송 버퍼(SQLite)와 로컬 TLS 인증서를 두는 곳 — gateway-data 볼륨
    data_dir: Path = Path("/data")
    # 운영 인증서를 마운트할 때만 지정. 비우면 data_dir/tls에 자체 서명 인증서를 만든다
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None

    def require_token_key(self) -> bytes:
        key = self.token_key.get_secret_value() if self.token_key else ""
        if len(key) < MIN_SECRET_LENGTH:
            raise ValueError(f"GATEWAY_TOKEN_KEY must be at least {MIN_SECRET_LENGTH} chars")
        return key.encode()

    def require_argus_ingest(self) -> tuple[str, bytes]:
        if not self.argus_ingest_url.startswith(("http://", "https://")):
            # urllib은 file:// 등도 연다 — 설정 실수로 로컬 파일을 읽는 일이 없게 스킴을 제한
            raise ValueError("GATEWAY_ARGUS_INGEST_URL must be an http(s) URL")
        secret = self.argus_ingest_secret.get_secret_value() if self.argus_ingest_secret else ""
        if not secret:
            raise ValueError("GATEWAY_ARGUS_INGEST_SECRET is required")
        return self.argus_ingest_url.rstrip("/"), secret.encode()
