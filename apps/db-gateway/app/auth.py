"""DB 접속 토큰 검증 + 계정 상태 확인 (architecture 3-4 "인증"·"계정 상태"·"토큰 검증 방식")

- 토큰 검증은 서명·만료·용도(aud)·아이디 일치만으로 한다.
  발급 기록(db_access_token)은 조회하지 않는다 — 공용 계정이 DB 소유자라
  게이트웨이로 접속한 사람이 그 테이블에 남의 토큰 행을 넣어 위장할 수 있기 때문
- 계정 상태(퇴직·잠금)는 operator를 조회해 확인한다
  (관리자 인증의 "매 요청 계정 상태 재확인"과 같은 원칙).
  같은 조회로 "존재하는 아이디인지"도 정한다 — 없는 아이디의 실패는 어디에도 남기지 않는다
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
import psycopg

AUDIENCE = "db-gateway"
MAX_FAILED_LOGINS = 5  # 플랫폼 관리자 잠금 기준과 같다 (platform-api auth/deps.py)


@dataclass(frozen=True)
class TokenCheck:
    ok: bool
    reason: str | None  # 실패 사유 코드 (원문 기록용 — 토큰 값은 담지 않는다)
    token_id: str | None  # 서명이 유효했을 때만 — 위조 토큰의 jti는 믿을 수 없다
    expires_at: datetime | None


def check_token(token: str, key: bytes, login_id: str, now: datetime | None = None) -> TokenCheck:
    now = now or datetime.now(UTC)
    try:
        # 1단계: 서명만 확인(알고리즘 고정 — 토큰 헤더의 alg를 믿지 않는다).
        # 만료·용도는 아래에서 따로 본다 — 서명이 맞으면 실패 기록에 토큰 ID를 남길 수 있게
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            options={
                "verify_exp": False,
                "verify_aud": False,
                "require": ["sub", "jti", "aud", "exp"],
            },
        )
        token_id = str(uuid.UUID(str(claims["jti"])))
        expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
        return TokenCheck(False, "INVALID_TOKEN", None, None)

    if claims["aud"] != AUDIENCE:
        reason = "WRONG_AUDIENCE"
    elif claims["sub"] != login_id:
        # 남의 토큰을 자기 아이디로 쓰는 경우 — 토큰 주인과 아이디가 같아야 한다
        reason = "TOKEN_OWNER_MISMATCH"
    elif expires_at <= now:
        reason = "TOKEN_EXPIRED"
    else:
        return TokenCheck(True, None, token_id, expires_at)
    return TokenCheck(False, reason, token_id, expires_at)


@dataclass(frozen=True)
class OperatorStatus:
    employment_status: str
    failed_login_count: int

    @property
    def blocked_reason(self) -> str | None:
        if self.employment_status != "ACTIVE":
            return "ACCOUNT_DISABLED"
        if self.failed_login_count >= MAX_FAILED_LOGINS:
            return "ACCOUNT_LOCKED"
        return None


async def fetch_operator_status(conninfo: str, login_id: str) -> OperatorStatus | None:
    """사용자 연결과 별개인 게이트웨이 자체 연결로 조회. 실패하면 예외 — 호출 측이 접속 거부"""
    async with await psycopg.AsyncConnection.connect(conninfo, connect_timeout=5) as conn:
        cur = await conn.execute(
            "SELECT employment_status, failed_login_count FROM operator WHERE login_id = %s",
            (login_id,),
        )
        row = await cur.fetchone()
    return None if row is None else OperatorStatus(row[0], row[1])
