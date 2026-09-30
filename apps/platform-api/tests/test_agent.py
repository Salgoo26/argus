"""접속기록 Agent — 무엇을 기록하고 무엇을 기록하지 않는가 (architecture 3절, api-spec 2절)"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.agent import access_log, access_log_exempt, record_subjects
from app.agent.context import AccessRecord
from app.agent.decorators import AccessLogSpec, check_admin_routes
from app.agent.middleware import build_event, query_keys
from app.auth.deps import CurrentOperator
from app.main import create_app
from app.models import member

from conftest import TEST_CLIENT_ADDR, add_operator, login, outbox_payloads


def _add_members(engine, count: int) -> None:
    rows = [
        {
            "email": f"user{n:04d}@example.com",
            "password_hash": "unusable",
            "name": f"회원{n}",  # 가상
            "phone": f"010-0000-{n:04d}",
            "address": "서울특별시 가상구 가상로 1",
        }
        for n in range(1, count + 1)
    ]
    with engine.begin() as conn:
        conn.execute(insert(member), rows)


@pytest.fixture
def logged_in(client, engine, password_hash):
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    return client


def _only(engine, action: str) -> dict:
    [event] = [p for p in outbox_payloads(engine) if p["action"] == action]
    return event


# ── 시나리오: 회원 목록 120건 다운로드 ─────────────────────


def test_download_is_recorded_with_member_ids_only(logged_in, engine):
    _add_members(engine, 150)

    res = logged_in.get("/admin/members/export", params={"limit": 120})

    assert res.status_code == 200
    event = _only(engine, "DOWNLOAD")
    assert event["actor"] == {"login_id": "ops_park"}  # §2 3호 식별자
    assert event["client_ip"] == TEST_CLIENT_ADDR[0]  # 접속지
    assert event["data_category"] == "MEMBER_BASIC" and event["result"] == "SUCCESS"
    assert event["access_path"] == "APP"
    assert event["subject"]["type"] == "MEMBER"
    assert event["subject"]["count"] == 120 and len(event["subject"]["ids"]) == 120
    assert event["subject"]["truncated"] is False
    assert event["request"] == {
        "method": "GET",
        "path": "/admin/members/export",
        "query_keys": ["limit"],
    }
    # 접속일시는 오프셋 포함 ISO 8601, 마이크로초까지
    occurred = datetime.fromisoformat(event["occurred_at"])
    assert occurred.tzinfo is not None
    assert abs(datetime.now(UTC) - occurred) < timedelta(minutes=1)


def test_payload_has_no_original_personal_information(logged_in, engine):
    # 절대 규칙 #3 — 정보주체는 내부 PK만. 이름·이메일·전화·주소가 실리면 안 된다
    _add_members(engine, 5)
    logged_in.get("/admin/members")
    logged_in.get("/admin/members/export")

    dumped = json.dumps(outbox_payloads(engine), ensure_ascii=False)
    for leaked in ("@example.com", "회원1", "010-0000", "가상로", "박지훈"):
        assert leaked not in dumped


def test_search_values_are_not_recorded_only_keys(logged_in, engine):
    _add_members(engine, 3)
    logged_in.get(
        "/admin/members/export",
        params={"joined_from": "2026-09-16", "joined_to": "2026-09-30", "limit": 7},
    )
    event = _only(engine, "DOWNLOAD")
    assert event["request"]["query_keys"] == ["joined_from", "joined_to", "limit"]
    assert "2026-09-16" not in json.dumps(event)


def test_list_is_recorded_as_read_of_displayed_members(logged_in, engine):
    _add_members(engine, 30)
    logged_in.get("/admin/members", params={"page": 2, "size": 20})
    event = _only(engine, "READ")
    assert event["subject"]["ids"] == [str(n) for n in range(21, 31)]  # 화면에 표시된 10명
    assert event["subject"]["count"] == 10


# ── 로그인 ────────────────────────────────────────────────


def test_login_success_is_recorded(client, engine, password_hash):
    add_operator(engine, password_hash)
    login(client)
    event = _only(engine, "LOGIN")
    assert event["result"] == "SUCCESS"
    assert event["data_category"] == "NONE"
    assert "subject" not in event  # LOGIN은 정보주체 생략 (api-spec 2-2)


def test_failed_login_of_existing_account_is_recorded(client, engine, password_hash):
    add_operator(engine, password_hash)
    login(client, password="wrong-password")
    event = _only(engine, "LOGIN")
    assert event["result"] == "FAILURE" and event["actor"]["login_id"] == "ops_park"


def test_login_with_unknown_id_is_not_recorded(client, engine):
    # ID 칸에 비밀번호를 잘못 넣은 경우 등 — 원장(append-only)에 영구히 남지 않게
    login(client, login_id="my-secret-password!")
    assert outbox_payloads(engine) == []


def test_unauthenticated_request_is_not_recorded(client, engine):
    assert client.get("/admin/members").status_code == 401
    assert outbox_payloads(engine) == []  # 식별자가 없다 = 취급자 행위가 아니다


def test_logout_is_explicitly_exempt(logged_in, engine):
    logged_in.post("/admin/auth/logout")
    assert [p["action"] for p in outbox_payloads(engine)] == ["LOGIN"]


# ── CLAUDE.md M2 필수 테스트 ──────────────────────────────


def test_customer_routes_are_not_recorded(settings, engine, password_hash):
    # 절대 규칙 #4 — 고객(정보주체) 행위는 접속기록 대상이 아니다
    app = create_app(settings)

    @app.get("/shop/members/me")
    def my_page():
        record_subjects(["10001"])  # 실수로 불러도 기록지가 없어 무시된다
        return {"ok": True}

    with TestClient(app, client=TEST_CLIENT_ADDR) as client:
        assert client.get("/shop/members/me").status_code == 200
        assert client.get("/healthz").status_code == 200
    app.state.engine.dispose()
    assert outbox_payloads(engine) == []


def test_business_failure_is_still_recorded_as_failure(settings, engine, password_hash):
    # 절대 규칙 #6 — 업무가 실패(예외·롤백)해도 시도는 FAILURE로 남는다
    app = create_app(settings)

    @app.post("/admin/members/{member_id}/grade")
    @access_log(action="UPDATE", data_category="MEMBER_BASIC")
    def change_grade(member_id: int, _operator: CurrentOperator):
        record_subjects([member_id])  # 업무 로직보다 먼저 대상을 적는다
        raise RuntimeError("business logic failed")

    add_operator(engine, password_hash)
    with TestClient(app, client=TEST_CLIENT_ADDR, raise_server_exceptions=False) as client:
        login(client)
        res = client.post("/admin/members/10001/grade")
    app.state.engine.dispose()

    assert res.status_code == 500
    event = _only(engine, "UPDATE")
    assert event["result"] == "FAILURE"
    assert event["subject"]["ids"] == ["10001"]
    assert event["request"]["path"] == "/admin/members/{member_id}/grade"  # 실제 값 대신 템플릿


def test_rejected_request_is_recorded_as_failure(logged_in, engine):
    # 인증된 취급자의 잘못된 요청(400)도 시도로 남긴다 — 정보주체를 정하기 전이라 건수 0
    assert logged_in.get("/admin/members/export", params={"limit": 0}).status_code == 400
    event = _only(engine, "DOWNLOAD")
    assert event["result"] == "FAILURE"
    assert event["subject"] == {"type": "MEMBER", "ids": [], "count": 0, "truncated": False}


# ── fail-closed ───────────────────────────────────────────


def test_response_is_blocked_when_log_cannot_be_stored(logged_in, engine, monkeypatch):
    _add_members(engine, 3)

    def broken_enqueue(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr("app.agent.middleware.enqueue", broken_enqueue)
    res = logged_in.get("/admin/members/export")

    # 기록할 수 없으면 내보내지 않는다 — CSV 대신 500
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "ACCESS_LOG_UNAVAILABLE"
    assert b"@example.com" not in res.content
    assert "set-cookie" not in res.headers


# ── 경로 변수·잘림 ───────────────────────────────────────


def test_path_variables_are_not_recorded(settings, engine, password_hash):
    app = create_app(settings)

    @app.get("/admin/members/search/{keyword}")
    @access_log(action="READ", data_category="MEMBER_BASIC")
    def search(keyword: str, _operator: CurrentOperator):
        record_subjects([])
        return {"keyword_length": len(keyword)}

    add_operator(engine, password_hash)
    with TestClient(app, client=TEST_CLIENT_ADDR) as client:
        login(client)
        client.get("/admin/members/search/홍길동")
    app.state.engine.dispose()

    event = _only(engine, "READ")
    assert event["request"]["path"] == "/admin/members/search/{keyword}"
    assert "홍길동" not in json.dumps(event, ensure_ascii=False)


def test_subject_ids_over_1000_are_truncated_with_full_count():
    record = AccessRecord(datetime.now(UTC), "203.0.113.10", "GET", [])
    record.actor_login_id = "ops_park"
    record.subject_ids = [str(n) for n in range(1500)]
    record.subject_count = 1500

    event = build_event(AccessLogSpec("DOWNLOAD", "MEMBER_BASIC"), record, "/x", "SUCCESS")

    assert len(event["subject"]["ids"]) == 1000
    assert event["subject"]["count"] == 1500
    assert event["subject"]["truncated"] is True


def test_query_keys_keep_names_only_and_drop_malformed():
    assert query_keys(b"b=1&a=2&b=3&items[0]=x") == ["b", "a", "items[0]"]
    # 키 자리에 값처럼 생긴 것(공백·@·한글)이 오면 버린다
    assert query_keys(b"hong gil dong@example.com=1&%ED%99%8D=1&ok=1") == ["ok"]


# ── 문패 검사 (기동 거부) ─────────────────────────────────


def test_admin_route_without_access_log_blocks_startup():
    app = FastAPI()

    @app.get("/admin/orders/{order_id}")
    def forgotten(order_id: int):
        return {}

    with pytest.raises(RuntimeError, match=r"GET /admin/orders/\{order_id\}"):
        check_admin_routes(app)


def test_exempt_and_non_admin_routes_pass_the_check():
    app = FastAPI()

    @app.post("/admin/auth/logout")
    @access_log_exempt("개인정보 처리 없음")
    def logout():
        return None

    @app.get("/shop/products")
    def products():
        return []

    check_admin_routes(app)  # 예외 없음


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "VIEW", "data_category": "MEMBER_BASIC"},
        {"action": "EXPORT", "data_category": "MEMBER_BASIC"},  # Argus 전용
        {"action": "READ", "data_category": "ACCESS_LOG"},  # Argus 전용
        {"action": "LOGIN", "data_category": "MEMBER_BASIC"},
        {"action": "READ", "data_category": "NONE"},
    ],
)
def test_invalid_access_log_spec_fails_at_import(kwargs):
    with pytest.raises(ValueError):
        access_log(**kwargs)


def test_exempt_requires_reason():
    with pytest.raises(ValueError):
        access_log_exempt("  ")


def test_spoofed_forwarded_for_is_ignored_without_trusted_proxy(logged_in, engine):
    # 신뢰 프록시가 없으면 클라이언트가 보낸 X-Forwarded-For는 무시 (절대 규칙 #10)
    logged_in.get("/admin/members", headers={"X-Forwarded-For": "8.8.8.8"})
    assert _only(engine, "READ")["client_ip"] == TEST_CLIENT_ADDR[0]


def test_request_state_is_isolated_between_requests(logged_in, engine):
    # 기록지는 요청마다 따로 — 앞 요청의 정보주체가 다음 요청에 섞이지 않는다
    _add_members(engine, 3)
    logged_in.get("/admin/members/export")
    logged_in.get("/admin/members/export", params={"joined_from": "2099-01-01"})
    events = [p for p in outbox_payloads(engine) if p["action"] == "DOWNLOAD"]
    assert [e["subject"]["count"] for e in events] == [3, 0]
