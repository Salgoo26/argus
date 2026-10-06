"""DB 접속 토큰 발급 (기능 레이어 8 — 2티어, architecture 3-4)

DB 툴(DBeaver 등)은 db-gateway로만 접속하고, 비밀번호 칸에 이 토큰을 넣는다.
게이트웨이는 토큰으로 "이 연결의 실사용자 = 플랫폼 아이디"를 확인한 뒤 공용 계정으로 DB에 중계한다.

- 서명 토큰(JWT HS256): sub=플랫폼 아이디, jti=토큰 ID, aud=db-gateway, exp=발급 후 1시간(고정)
- 서명 키는 관리자 로그인 키와 별개(DB_GATEWAY_TOKEN_KEY) — 관리자 쿠키를 DB 비밀번호로 못 쓰게
- 발급 기록(db_access_token)은 감사용이며 토큰 값은 저장하지 않는다 — 응답으로 한 번만 보여준다
- 사유 입력 없음(사용자 결정 — 이상 징후는 소명 단계에서), 재직 중인 관리자 누구나 발급
- 이 라우트 자체는 3티어 접속기록 대상이 아니다(2026-10-06 사용자 결정 — 개인정보 처리 없음).
  발급 사실은 db_access_token과, 그 토큰으로 접속한 게이트웨이 LOGIN 기록의 token_id로 추적한다
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Request
from sqlalchemy import insert

from app.agent import access_log_exempt
from app.auth.deps import CurrentOperator
from app.errors import ApiError
from app.models import db_access_token
from app.shop.deps import client_ip

router = APIRouter(prefix="/admin/db-tokens")

AUDIENCE = "db-gateway"
LIFETIME = timedelta(hours=1)  # db-schema 4절 CHECK와 같은 값


@router.post("", status_code=201)
@access_log_exempt("DB 접속 토큰 발급 — 개인정보 처리 없음 (발급 기록은 db_access_token)")
def issue_db_token(operator: CurrentOperator, request: Request) -> dict:
    # 발급 IP는 관리자 접속기록과 같은 신뢰 프록시 규칙으로 정한다 (CLAUDE.md 3절 #10).
    # 정할 수 없으면 발급하지 않는다 — 발급 기록의 접속지를 지어내지 않는다
    ip = client_ip(request)
    if ip is None:
        raise ApiError(400, "CLIENT_IP_UNAVAILABLE", "client address is not available")

    # JWT의 시각은 초 단위 — 발급 기록과 토큰의 만료 시각이 정확히 같도록 초로 맞춘다
    issued_at = datetime.now(UTC).replace(microsecond=0)
    expires_at = issued_at + LIFETIME
    token_id = uuid.uuid4()
    token = jwt.encode(
        {
            "sub": operator.login_id,
            "jti": str(token_id),
            "aud": AUDIENCE,
            "iat": issued_at,
            "exp": expires_at,
        },
        request.app.state.db_gateway_token_key,
        algorithm="HS256",
    )

    with request.app.state.engine.begin() as conn:
        conn.execute(
            insert(db_access_token).values(
                token_id=token_id,
                operator_id=operator.id,
                issued_at=issued_at,
                expires_at=expires_at,
                issued_ip=ip,
            )
        )

    return {
        "token": token,
        "token_id": str(token_id),
        "login_id": operator.login_id,  # DB 툴의 사용자 이름 칸에 넣을 값
        "expires_at": expires_at.isoformat(),
    }
