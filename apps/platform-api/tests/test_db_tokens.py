"""DB 접속 토큰 발급 (기능 레이어 8 — 2티어, architecture 3-4)"""

import uuid
from datetime import datetime

import jwt
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from app.config import Settings
from app.main import create_app
from app.models import db_access_token, operator

from conftest import (
    TEST_AUTH_SECRET,
    TEST_CLIENT_ADDR,
    TEST_DB_GATEWAY_TOKEN_KEY,
    add_operator,
    login,
    outbox_payloads,
)

URL = "/admin/db-tokens"


@pytest.fixture
def admin(client, engine, password_hash):
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    return client


def _decode(token: str, key: str = TEST_DB_GATEWAY_TOKEN_KEY) -> dict:
    return jwt.decode(token, key, algorithms=["HS256"], audience="db-gateway")


def _rows(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(db_access_token)).mappings()]


def test_issues_signed_token_for_one_hour(admin, engine):
    res = admin.post(URL)
    assert res.status_code == 201
    body = res.json()
    assert body["login_id"] == "ops_park"

    claims = _decode(body["token"])
    assert claims["sub"] == "ops_park"  # 실사용자 = 플랫폼 아이디
    assert claims["jti"] == body["token_id"]
    assert claims["exp"] - claims["iat"] == 3600  # 1시간 고정
    assert datetime.fromisoformat(body["expires_at"]).timestamp() == claims["exp"]


def test_issue_is_recorded_without_token_value(admin, engine):
    body = admin.post(URL).json()
    [row] = _rows(engine)
    assert row["token_id"] == uuid.UUID(body["token_id"])
    assert str(row["issued_ip"]) == TEST_CLIENT_ADDR[0]
    assert row["expires_at"].timestamp() == _decode(body["token"])["exp"]
    # 토큰 값·해시는 저장하지 않는다 — 발급 화면에 한 번만
    assert body["token"] not in str(row)


def test_each_issue_is_a_new_token(admin, engine):
    first, second = admin.post(URL).json(), admin.post(URL).json()
    assert first["token_id"] != second["token_id"]
    assert len(_rows(engine)) == 2


def test_issue_is_not_access_logged(admin, engine):
    # 사용자 결정(2026-10-06): 개인정보 처리가 아니라 3티어 접속기록 대상에서 제외
    before = len(outbox_payloads(engine))
    admin.post(URL)
    assert len(outbox_payloads(engine)) == before


def test_requires_admin_login(client, engine):
    assert client.post(URL).status_code == 401
    assert _rows(engine) == []


def test_disabled_operator_cannot_issue(admin, engine):
    # 로그인 뒤 퇴직 처리되면 즉시 차단 (매 요청 계정 상태 재확인)
    with engine.begin() as conn:
        conn.execute(update(operator).values(employment_status="TERMINATED"))
    assert admin.post(URL).status_code == 401
    assert _rows(engine) == []


def test_token_is_not_valid_as_admin_session_and_vice_versa(admin):
    token = admin.post(URL).json()["token"]
    # 관리자 로그인 키로는 검증되지 않는다 (키 분리)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, TEST_AUTH_SECRET, algorithms=["HS256"], audience="db-gateway")
    # DB 토큰을 관리자 쿠키에 넣어도 관리자 API를 쓸 수 없다
    admin.cookies.clear()
    admin.cookies.set("platform_session", token)
    assert admin.get("/admin/auth/me").status_code == 401


@pytest.mark.parametrize("key", [None, "short", TEST_AUTH_SECRET])
def test_api_refuses_to_start_without_valid_token_key(settings: Settings, key):
    # 없음·짧음·관리자 로그인 키와 같음 → 기동 거부
    with pytest.raises(ValueError, match="DB_GATEWAY_TOKEN_KEY"):
        secret = SecretStr(key) if key else None
        create_app(settings.model_copy(update={"db_gateway_token_key": secret}))
