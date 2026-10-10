"""접속기록 조회·검색 API — 필터, 담당자 전용, 마스킹, 자체 접속기록 (LOG-02, 기능 레이어 3)"""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.detection.batch import run_batch
from app.ledger.append import append_access_logs
from app.models import access_log, argus_user, handler, source_system

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
KST = timezone(timedelta(hours=9))
SEARCH = "/api/access-logs/search"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
    """담당자 officer, 취급자 ops_park (가상 인물). 룰은 대량 다운로드만"""
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    with admin_engine.begin() as conn:
        conn.execute(
            insert(argus_user).values(
                login_id="officer", password_hash=password_hash, role="OFFICER"
            )
        )
        handler_id = conn.execute(
            insert(handler)
            .values(
                source_system_id=1,
                login_id="ops_park",
                name="박지훈",
                team="OPS",
                employment_status="ACTIVE",
                last_event_at=datetime.now(UTC),
            )
            .returning(handler.c.id)
        ).scalar_one()
        conn.execute(
            insert(argus_user).values(
                login_id="ops_park",
                password_hash=password_hash,
                role="HANDLER",
                handler_id=handler_id,
            )
        )
    yield
    with admin_engine.begin() as conn:
        for table in (
            "detection_log",
            "detection_status_history",
            "explanation",
            "detection",
            "detection_batch_run",
            "argus_user",
            "handler",
        ):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름


@pytest.fixture
def as_user(app):
    clients = []

    def login(login_id: str) -> TestClient:
        c = TestClient(app, client=TEST_CLIENT_ADDR)
        clients.append(c)
        res = c.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD})
        assert res.status_code == 200
        return c

    yield login
    for c in clients:
        c.close()


def add(app_engine, **overrides) -> int:
    with app_engine.begin() as conn:
        result = append_access_logs(conn, [make_entry(**overrides)], received_at=datetime.now(UTC))
    return result.inserted_ids[0]


def ids_of(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def argus_search_logs(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(access_log)
                .join(source_system, source_system.c.id == access_log.c.source_system_id)
                .where(source_system.c.code == "ARGUS", access_log.c.request_path == SEARCH)
                .order_by(access_log.c.id)
            ).mappings()
        ]


# ── 필터 ──────────────────────────────────────────────────


def test_default_is_platform_logs_of_the_last_7_days_newest_first(app_engine, as_user):
    now = datetime.now(UTC)
    old = add(app_engine, occurred_at=now - timedelta(days=10))
    earlier = add(app_engine, occurred_at=now - timedelta(hours=2))
    latest = add(app_engine, occurred_at=now - timedelta(minutes=5))
    officer = as_user("officer")  # 이 로그인은 ARGUS 출처 기록 — 기본 검색에 나오지 않는다

    response = officer.post(SEARCH, json={})

    assert ids_of(response) == [latest, earlier]
    assert old not in ids_of(response)
    assert response.json()["total"] == 2


def test_filters_narrow_the_result(app_engine, as_user):
    now = datetime.now(UTC)
    download = add(
        app_engine,
        action="DOWNLOAD",
        subject_ids=[str(n) for n in range(10001, 10121)],
        subject_count=120,
        occurred_at=now,
    )
    other_actor = add(app_engine, actor_login_id="mkt_lee", subject_ids=["10050"], occurred_at=now)
    db_path = add(app_engine, access_path="DB", subject_ids=["10999"], occurred_at=now)
    officer = as_user("officer")

    assert ids_of(officer.post(SEARCH, json={"actor": "mkt_lee"})) == [other_actor]
    assert ids_of(officer.post(SEARCH, json={"action": "DOWNLOAD"})) == [download]
    assert ids_of(officer.post(SEARCH, json={"access_path": "DB"})) == [db_path]
    # 정보주체: 저장값과 화면 표기 둘 다 받는다
    assert ids_of(officer.post(SEARCH, json={"subject": "10050"})) == [other_actor, download]
    assert ids_of(officer.post(SEARCH, json={"subject": "member_10050"})) == [
        other_actor,
        download,
    ]
    assert ids_of(officer.post(SEARCH, json={"subject": "10050", "actor": "ops_park"})) == [
        download
    ]


def test_period_uses_korean_dates(app_engine, as_user):
    # KST 9/29 00:30 = UTC 9/28 15:30 — UTC 날짜로 자르면 전날로 빠진다
    just_after_midnight = add(app_engine, occurred_at=datetime(2026, 9, 29, 0, 30, tzinfo=KST))
    add(app_engine, occurred_at=datetime(2026, 9, 28, 23, 59, tzinfo=KST))
    add(app_engine, occurred_at=datetime(2026, 9, 30, 0, 0, tzinfo=KST))
    officer = as_user("officer")

    response = officer.post(SEARCH, json={"date_from": "2026-09-29", "date_to": "2026-09-29"})

    assert ids_of(response) == [just_after_midnight]
    assert response.json()["period"]["from"].startswith("2026-09-29T00:00:00+09:00")


def test_argus_self_logs_can_be_selected(app_engine, as_user):
    officer = as_user("officer")
    response = officer.post(SEARCH, json={"source": "ARGUS"})
    [login] = response.json()["items"]  # 방금 한 로그인 (검색 기록은 응답 뒤에 남는다)
    assert login["source"] == "ARGUS" and login["action"] == "LOGIN"
    assert login["actor_login_id"] == "officer"


@pytest.mark.parametrize(
    "body",
    [
        {"date_from": "2025-01-01", "date_to": "2026-01-02"},  # 1년 초과
        {"date_from": "2026-09-30", "date_to": "2026-09-29"},  # 거꾸로
        {"action": "HACK"},
        {"source": "BANK"},
        {"subject": "100 50"},  # 공백
        {"size": 101},
    ],
    ids=["too-long", "reversed", "action", "source", "subject-space", "size"],
)
def test_invalid_conditions_are_400(as_user, body):
    assert as_user("officer").post(SEARCH, json=body).status_code == 400


# ── 결과: 마스킹·탐지건 연결 ──────────────────────────────


def test_subjects_are_masked_and_limited(app_engine, as_user):
    ids = [str(n) for n in range(10001, 10121)]
    add(app_engine, action="DOWNLOAD", subject_ids=ids, subject_count=120)
    officer = as_user("officer")

    response = officer.post(SEARCH, json={"subject": "10077"})

    [item] = response.json()["items"]
    assert item["subjects"] == ["member_10***"] * 5  # 앞 5개만, 마스킹
    assert item["subject_count"] == 120
    body = response.text
    assert not any(f'"{i}"' in body or f"member_{i}" in body for i in ids)


def test_detected_logs_link_to_their_cases(app_engine, as_user):
    downloaded = add(
        app_engine,
        action="DOWNLOAD",
        subject_ids=[str(n) for n in range(10001, 10121)],
        subject_count=120,
    )
    plain = add(app_engine)
    run_batch(app_engine)
    officer = as_user("officer")

    items = {i["id"]: i for i in officer.post(SEARCH, json={}).json()["items"]}

    assert len(items[downloaded]["detection_ids"]) == 1
    assert items[plain]["detection_ids"] == []
    assert items[downloaded]["actor_name"] == "박지훈"


# ── 권한·자체 접속기록 ────────────────────────────────────


def test_ip_category_and_result_filters(app_engine, as_user):
    # v0.1 보강 C-2 — 접속지(정확히 일치·앞부분 일치)·데이터 유형·결과
    office = add(app_engine, client_ip="10.20.3.55")
    vpn = add(app_engine, client_ip="10.20.30.7", data_category="PAYMENT")
    outside = add(app_engine, client_ip="203.0.113.9", result="FAILURE")
    v6 = add(app_engine, client_ip="2001:db8::5")
    officer = as_user("officer")

    assert ids_of(officer.post(SEARCH, json={"client_ip": "10.20.3.55"})) == [office]
    assert ids_of(officer.post(SEARCH, json={"client_ip": "10.20.3"})) == [vpn, office]
    assert ids_of(officer.post(SEARCH, json={"client_ip": "2001:db8:"})) == [v6]
    assert ids_of(officer.post(SEARCH, json={"data_category": "PAYMENT"})) == [vpn]
    assert ids_of(officer.post(SEARCH, json={"result": "FAILURE"})) == [outside]


@pytest.mark.parametrize(
    "body",
    [{"client_ip": "10.20.%"}, {"client_ip": "10_20"}, {"result": "OK"}, {"data_category": "X"}],
)
def test_invalid_new_conditions_are_400(as_user, body):
    # LIKE 와일드카드(% _)는 받지 않는다 — 앞부분 일치는 서버가 만든다
    assert as_user("officer").post(SEARCH, json=body).status_code == 400


# ── 취급자 "내 접속기록" (v0.1 보강 D) ──────────────────────


def test_handler_sees_only_own_platform_logs(app_engine, as_user):
    mine_app = add(app_engine, actor_login_id="ops_park")
    mine_db = add(
        app_engine, actor_login_id="ops_park", access_path="DB", context=DB_CONTEXT, subject_ids=[]
    )
    add(app_engine, actor_login_id="mkt_lee")  # 남의 기록
    handler_client = as_user("ops_park")  # 이 로그인은 Argus 자체 기록(ARGUS) — 대상 아님

    response = handler_client.post(SEARCH, json={})

    assert ids_of(response) == [mine_db, mine_app]  # 경로 APP·DB 모두, 행위자는 서버가 고정
    body = response.json()
    assert body["linked"] is True
    assert all(item["subjects"] in ([], ["member_10***"]) for item in body["items"])  # 마스킹


def test_handler_cannot_search_others_or_argus_logs(app_engine, as_user):
    add(app_engine, actor_login_id="mkt_lee")
    handler_client = as_user("ops_park")
    assert handler_client.post(SEARCH, json={"actor": "mkt_lee"}).status_code == 403
    assert handler_client.post(SEARCH, json={"source": "ARGUS"}).status_code == 403
    # 본인 아이디를 적은 요청은 그대로 본인 조회
    assert handler_client.post(SEARCH, json={"actor": "ops_park"}).status_code == 200


def test_handler_self_search_is_logged(app_engine, as_user):
    add(app_engine, actor_login_id="ops_park", subject_ids=["10050"])
    handler_client = as_user("ops_park")
    handler_client.post(SEARCH, json={"actor": "mkt_lee"})  # 거부된 시도도 남는다
    handler_client.post(SEARCH, json={"action": "READ"})

    denied, allowed = argus_search_logs(app_engine)
    assert denied["actor_login_id"] == "ops_park" and denied["result"] == "FAILURE"
    assert allowed["result"] == "SUCCESS" and allowed["request_query_keys"] == ["action"]
    assert allowed["subject_count"] == 1 and allowed["subject_ids"] is None


def test_handler_period_and_filters_still_apply(app_engine, as_user):
    now = datetime.now(UTC)
    add(app_engine, actor_login_id="ops_park", occurred_at=now - timedelta(days=10))
    recent = add(app_engine, actor_login_id="ops_park", action="DOWNLOAD", occurred_at=now)
    handler_client = as_user("ops_park")
    assert ids_of(handler_client.post(SEARCH, json={})) == [recent]  # 기본 최근 7일
    assert ids_of(handler_client.post(SEARCH, json={"action": "READ"})) == []


def test_search_is_logged_with_condition_names_but_not_values(app_engine, as_user):
    add(app_engine, action="DOWNLOAD", subject_ids=["10050", "10051"], subject_count=2)
    officer = as_user("officer")

    officer.post(SEARCH, json={"subject": "10050", "action": "DOWNLOAD"})

    [logged] = argus_search_logs(app_engine)
    assert logged["action"] == "READ" and logged["data_category"] == "ACCESS_LOG"
    assert logged["request_method"] == "POST"
    assert logged["request_query_keys"] == ["action", "subject"]  # 키 이름만 (policy 6-2)
    assert logged["subject_count"] == 2  # 화면에 보여 준 마스킹 값의 수
    assert logged["subject_ids"] is None  # 회원 PK를 Argus 원장에 다시 쌓지 않는다
    assert "10050" not in json.dumps(logged, default=str)  # 검색어 값은 어디에도 없다


def test_unauthenticated_is_401(client):
    assert client.post(SEARCH, json={}).status_code == 401


DB_CONTEXT = {
    "db_user": "platform_owner",
    "sql_normalized": "SELECT id, email FROM member WHERE email = $1",
    "tables": ["member"],
    "columns": ["member.id", "member.email"],
    "row_count": 1,
    "raw_ref": "6f1d0c2e-0000-4000-8000-000000000001",
    "raw_fingerprint": "sha256:" + "ab" * 32,
    "subject_unresolved": True,
    "token_id": "6f1d0c2e-0000-4000-8000-000000000002",
}


def test_db_records_show_normalized_sql_only(app_engine, as_user):
    # DB 직접(2티어) 기록은 정규화 SQL·테이블·건수를 보여 준다 — DB 계정·토큰 ID는 싣지 않는다
    add(app_engine, access_path="DB", subject_ids=[], context=DB_CONTEXT)
    add(app_engine)  # 화면 경유
    items = as_user("officer").post(SEARCH, json={}).json()["items"]
    by_path = {i["access_path"]: i for i in items}
    assert by_path["APP"]["db"] is None
    db = by_path["DB"]["db"]
    assert db["sql_normalized"] == DB_CONTEXT["sql_normalized"]
    assert db["tables"] == ["member"] and db["row_count"] == 1 and db["subject_unresolved"] is True
    assert "platform_owner" not in str(items) and DB_CONTEXT["token_id"] not in str(items)
