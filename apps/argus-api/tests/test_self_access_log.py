"""Argus 자체 접속기록 (LOG-17, api-spec 2-7) — 감시자의 행위도 같은 원장·해시체인에"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.agent import access_log, access_log_exempt, record_subject_count
from app.agent.decorators import check_api_routes
from app.auth.deps import CurrentUser
from app.auth.passwords import hash_password
from app.ledger.hashchain import verify_chain
from app.main import create_app
from app.models import access_log as access_log_table
from app.models import argus_user, source_system

from conftest import TEST_CLIENT_ADDR

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값


@pytest.fixture(autouse=True)
def officer(admin_engine):
    with admin_engine.begin() as conn:
        conn.execute(
            insert(argus_user).values(
                login_id="officer", password_hash=hash_password(PASSWORD), role="OFFICER"
            )
        )
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))


def login(client, login_id="officer", password=PASSWORD):
    return client.post("/api/auth/login", json={"login_id": login_id, "password": password})


def argus_logs(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        rows = conn.execute(
            select(access_log_table)
            .join(source_system, source_system.c.id == access_log_table.c.source_system_id)
            .where(source_system.c.code == "ARGUS")
            .order_by(access_log_table.c.id)
        ).mappings()
        return [dict(r) for r in rows]


def test_login_is_recorded_in_the_ledger_as_argus(client, app_engine):
    login(client, password="wrong-password")
    login(client)

    failure, success = argus_logs(app_engine)
    for row in (failure, success):
        assert row["actor_login_id"] == "officer"
        assert row["action"] == "LOGIN" and row["data_category"] == "NONE"
        assert str(row["client_ip"]) == TEST_CLIENT_ADDR[0]
        assert row["request_path"] == "/api/auth/login"
    assert (failure["result"], success["result"]) == ("FAILURE", "SUCCESS")
    with app_engine.connect() as conn:
        assert verify_chain(conn).ok  # 외부 출처와 같은 해시체인


def test_unknown_id_and_unauthenticated_requests_are_not_recorded(client, app_engine):
    login(client, login_id="my-secret-password!")
    client.get("/api/auth/me")
    assert argus_logs(app_engine) == []


def test_exempt_routes_are_not_recorded(client, app_engine):
    login(client)
    client.get("/api/auth/me")
    client.post("/api/auth/logout")
    assert [r["action"] for r in argus_logs(app_engine)] == ["LOGIN"]


def test_ingest_api_is_not_self_logged(client, app_engine):
    # 시스템 간 수신(/ingest)은 화면용 API가 아니다 — 수신한 기록만 원장에 들어간다
    client.post("/ingest/v1/access-logs", content=b"{}")
    assert argus_logs(app_engine) == []


def test_read_records_count_only_not_member_ids(settings, app_engine):
    # 탐지건 조회는 회원 PK를 다시 쌓지 않고 건수만 (policy 6-3).
    # 조회 API는 PR ②에서 만든다 — 여기선 테스트 라우트로 확인
    app = create_app(settings)

    @app.get("/api/detections/{detection_id}")
    @access_log(action="READ", data_category="ACCESS_LOG")
    def detail(detection_id: int, _user: CurrentUser):
        record_subject_count(120)
        return {"masked": ["member_10***"]}

    with TestClient(app, client=TEST_CLIENT_ADDR) as client:
        login(client)
        client.get("/api/detections/3?status=OPEN")
    app.state.engine.dispose()

    row = argus_logs(app_engine)[-1]
    assert row["action"] == "READ" and row["data_category"] == "ACCESS_LOG"
    assert row["subject_type"] == "MEMBER" and row["subject_count"] == 120
    assert row["subject_ids"] is None  # 회원 PK는 원장에 다시 적재되지 않는다
    assert row["request_path"] == "/api/detections/{detection_id}"  # 경로 변수 값 대신 템플릿
    assert row["request_query_keys"] == ["status"]


def test_response_is_blocked_when_self_log_fails(client, app_engine, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("app.agent.middleware.append_access_logs", broken)
    res = login(client)

    assert res.status_code == 500
    assert res.json()["error"]["code"] == "ACCESS_LOG_UNAVAILABLE"
    assert "set-cookie" not in res.headers  # 기록할 수 없으면 로그인도 성립하지 않는다


def test_api_route_without_access_log_blocks_startup():
    app = FastAPI()

    @app.get("/api/detections")
    def forgotten():
        return []

    @app.post("/api/auth/logout")
    @access_log_exempt("개인정보 처리 없음")
    def logout():
        return None

    with pytest.raises(RuntimeError, match="GET /api/detections"):
        check_api_routes(app)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "DOWNLOAD", "data_category": "ACCESS_LOG"},  # 플랫폼 행위 코드
        {"action": "READ", "data_category": "MEMBER_BASIC"},
        {"action": "LOGIN", "data_category": "ACCESS_LOG"},
    ],
)
def test_invalid_argus_access_log_spec(kwargs):
    with pytest.raises(ValueError):
        access_log(**kwargs)
