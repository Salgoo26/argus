"""보호 대상 등록부 (v0.1 보강 N — 개인정보 식별·관리)

N-1 등록부(담당자 전용, 사유 필수, 이력 append-only, 해제 = 비활성), 초기 등록 이관
N-2 DB 구조 목록 수신(이름·자료형만 — 값 칸은 거부), 미분류 표시
N-3 게이트웨이에 내려주는 판정 표(테이블 = 가장 민감한 컬럼 유형, 회원 식별 열, 버전)
N-4 현황(미분류 수·마지막 수신·2년 보관 대상·테이블별 30일 DB 직접 접근)
"""

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from app.auth.passwords import hash_password
from app.ingest.auth import sign
from app.ledger.append import append_access_logs
from app.models import argus_user, handler, protected_column, protected_column_history
from app.protection.registry import build_registry

from conftest import TEST_CLIENT_ADDR, TEST_SECRET, make_entry, signed_headers

PASSWORD = "protection-test-password-1"  # 테스트 전용 더미 값
SCHEMA = "/ingest/v1/db-schema"
REGISTRY = "/ingest/v1/protection-registry"

# 플랫폼 DB 구조(축약) — 이름·자료형만
PLATFORM_SCHEMA = {
    "database": "platform",
    "tables": [
        {
            "name": "member",
            "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "email", "type": "character varying(255)"},
                {"name": "name", "type": "character varying(50)"},
                {"name": "rrn_enc", "type": "bytea"},  # 새로 생긴 컬럼 — 미분류
            ],
        },
        {"name": "operator", "columns": [{"name": "login_id", "type": "character varying(64)"}]},
    ],
}


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(scope="module")
def seeded_registry(admin_engine) -> list[dict]:
    """마이그레이션의 초기 등록 — 테스트가 바꾼 등록부를 되돌릴 기준"""
    with admin_engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(protected_column)).mappings()]


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seeded_registry):
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
                name="박지훈",  # 가상 인물
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
    seed_ids = [r["id"] for r in seeded_registry]
    with admin_engine.begin() as conn:
        # 등록부를 초기 등록 상태로 되돌린다 (이력 정리는 테스트에서만 트리거를 끈다)
        conn.exec_driver_sql(
            "ALTER TABLE protected_column_history DISABLE TRIGGER trg_protected_history_append_only"
        )
        conn.execute(
            text("DELETE FROM protected_column_history WHERE column_id <> ALL(:ids)"),
            {"ids": seed_ids},
        )
        conn.execute(
            text(
                "DELETE FROM protected_column_history WHERE id NOT IN"
                " (SELECT min(id) FROM protected_column_history GROUP BY column_id)"
            )
        )
        conn.exec_driver_sql(
            "ALTER TABLE protected_column_history ENABLE TRIGGER trg_protected_history_append_only"
        )
        conn.execute(text("DELETE FROM protected_column WHERE id <> ALL(:ids)"), {"ids": seed_ids})
        for seed in seeded_registry:
            conn.execute(
                protected_column.update()
                .where(protected_column.c.id == seed["id"])
                .values(
                    item=seed["item"],
                    data_category=seed["data_category"],
                    member_key=seed["member_key"],
                    active=seed["active"],
                    updated_by=seed["updated_by"],
                )
            )
        conn.execute(text("DELETE FROM db_schema_column"))
        conn.execute(text("DELETE FROM db_schema_receipt"))
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))


@pytest.fixture
def as_user(app):
    clients = []

    def login(login_id: str) -> TestClient:
        c = TestClient(app, client=TEST_CLIENT_ADDR)
        clients.append(c)
        res = c.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD})
        assert res.status_code == 200, res.text
        return c

    yield login
    for c in clients:
        c.close()


def push_schema(client, payload=None, **header_kwargs):
    body = json.dumps(payload or PLATFORM_SCHEMA).encode()
    return client.post(SCHEMA, content=body, headers=signed_headers(body, **header_kwargs))


def fetch_registry(client, secret: str = TEST_SECRET):
    ts = str(int(time.time()))
    headers = {
        "X-Argus-Source": "PLATFORM",
        "X-Argus-Timestamp": ts,
        "X-Argus-Signature": sign(secret.encode(), ts, b""),
    }
    return client.get(REGISTRY, headers=headers)


def register(client, **body):
    return client.post("/api/protection/columns", json={"reason": "개인정보 영향평가 반영"} | body)


# ── N-1 초기 등록 이관 ─────────────────────────────────────


def test_initial_registry_matches_the_old_fixed_tables(app_engine):
    # 게이트웨이에 고정돼 있던 TABLE_CATEGORY·MEMBER_COLUMNS와 같은 판정이 나와야 한다
    with app_engine.connect() as conn:
        registry = build_registry(conn)
    assert registry["tables"] == {
        "member": "MEMBER_BASIC",
        "member_consent": "MEMBER_BASIC",
        "retained_member_record": "MEMBER_BASIC",
        "shipping_address": "MEMBER_BASIC",
        "refund_account": "PAYMENT",
        "inquiry": "INQUIRY",
        "orders": "ORDER",
        "payment": "ORDER",
    }
    assert registry["member_columns"] == sorted(
        [
            ["member", "id"],
            ["orders", "member_id"],
            ["member_consent", "member_id"],
            ["refund_account", "member_id"],
            ["inquiry", "member_id"],
            ["shipping_address", "member_id"],
            ["retained_member_record", "original_member_id"],
        ]
    )
    assert registry["version"].startswith("r") and registry["database"] == "platform"
    with app_engine.connect() as conn:
        reasons = set(conn.execute(select(protected_column_history.c.reason)).scalars())
    assert reasons == {"초기 등록 — 게이트웨이 고정 표 이관"}


# ── N-2 DB 구조 목록 수신 ──────────────────────────────────


def test_schema_push_needs_a_signature(client):
    assert push_schema(client, secret="wrong-secret-0000000000000000").status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        # 값·예시를 실을 수 있는 칸은 받지 않는다
        {
            "database": "platform",
            "tables": [
                {"name": "member", "columns": [{"name": "email", "type": "text", "sample": "x"}]}
            ],
        },
        {"database": "platform", "tables": [{"name": "member", "columns": [], "rows": 3}]},
        {"database": "other_db", "tables": []},
        {"database": "platform", "tables": [{"name": "drop table;", "columns": []}]},
        {"database": "platform", "tables": [], "values": []},
    ],
    ids=["column-sample", "table-rows", "unknown-db", "bad-name", "extra-key"],
)
def test_schema_with_value_slots_or_bad_names_is_rejected(client, payload):
    res = push_schema(client, payload)
    assert res.status_code == 400


def test_schema_push_replaces_the_list_and_shows_unclassified(client, as_user):
    assert push_schema(client).json() == {"tables": 2, "columns": 5}
    overview = as_user("officer").get("/api/protection").json()
    member = next(t for t in overview["tables"] if t["table_name"] == "member")
    status = {c["column_name"]: c["status"] for c in member["columns"]}
    assert status["email"] == "PERSONAL" and status["id"] == "PERSONAL"
    assert status["rrn_enc"] == "UNCLASSIFIED"  # 새 컬럼 — 누락 사례 ①의 DB판
    assert status["phone"] == "MISSING"  # 등록됐지만 받은 구조에 없음
    operator = next(t for t in overview["tables"] if t["table_name"] == "operator")
    assert [c["status"] for c in operator["columns"]] == ["UNCLASSIFIED"]
    assert overview["summary"]["unclassified"] == 2
    assert overview["summary"]["last_schema_at"] is not None

    # 다음 수신이 목록을 통째로 바꾼다
    smaller = {"database": "platform", "tables": [PLATFORM_SCHEMA["tables"][1]]}
    assert push_schema(client, smaller).json() == {"tables": 1, "columns": 1}


# ── N-1 등록·변경·해제 ────────────────────────────────────


def test_register_change_and_release_with_reason(client, as_user, app_engine):
    push_schema(client)
    officer = as_user("officer")
    before = fetch_registry(client).json()["version"]

    res = register(
        officer,
        table_name="member",
        column_name="rrn_enc",
        item="UNIQUE_ID",
        data_category="MEMBER_BASIC",
    )
    assert res.status_code == 200 and res.json()["change_type"] == "REGISTER"
    assert res.json()["version"] != before  # 등록부 버전이 바뀐다
    overview = officer.get("/api/protection").json()
    assert overview["summary"]["two_year_retention"] is True  # 고유식별정보 → 2년 보관 대상
    assert overview["summary"]["two_year_items"] == ["UNIQUE_ID"]

    changed = register(
        officer,
        table_name="member",
        column_name="rrn_enc",
        item="SENSITIVE",
        data_category="MEMBER_BASIC",
    )
    assert changed.json()["change_type"] == "CHANGE"
    same = register(
        officer,
        table_name="member",
        column_name="rrn_enc",
        item="SENSITIVE",
        data_category="MEMBER_BASIC",
    )
    assert same.status_code == 409

    column_id = res.json()["id"]
    released = officer.post(
        f"/api/protection/columns/{column_id}/release", json={"reason": "컬럼 폐기 예정"}
    )
    assert released.status_code == 200
    with app_engine.connect() as conn:
        row = conn.execute(select(protected_column).where(protected_column.c.id == column_id)).one()
    assert row.active is False  # 삭제가 아니라 비활성
    history = officer.get("/api/protection/history").json()["items"]
    assert [h["change_type"] for h in history[:3]] == ["RELEASE", "CHANGE", "REGISTER"]
    assert history[0]["changed_by"] == "officer" and history[0]["reason"] == "컬럼 폐기 예정"


def test_not_personal_is_an_explicit_classification(client, as_user):
    push_schema(client)
    officer = as_user("officer")
    res = register(officer, table_name="operator", column_name="login_id", item="NOT_PERSONAL")
    assert res.status_code == 200
    operator = next(
        t for t in officer.get("/api/protection").json()["tables"] if t["table_name"] == "operator"
    )
    assert operator["columns"][0]["status"] == "NOT_PERSONAL"
    assert operator["data_category"] is None  # 개인정보 컬럼이 없으면 판정 표에 없다


@pytest.mark.parametrize(
    "body",
    [
        {"item": "EMAIL"},  # 개인정보인데 데이터 유형 없음
        {"item": "NOT_PERSONAL", "data_category": "MEMBER_BASIC"},
        {"item": "EMAIL", "data_category": "MEMBER_BASIC", "member_key": True},
        {"item": "EMAIL", "data_category": "MEMBER_BASIC", "reason": "  "},
    ],
    ids=["no-category", "not-personal-with-category", "member-key-not-id", "blank-reason"],
)
def test_inconsistent_registration_is_400(client, as_user, body):
    push_schema(client)
    res = register(as_user("officer"), table_name="member", column_name="rrn_enc", **body)
    assert res.status_code == 400


def test_only_columns_in_the_received_structure(client, as_user):
    push_schema(client)
    res = register(
        as_user("officer"),
        table_name="member",
        column_name="emial",
        item="EMAIL",
        data_category="MEMBER_BASIC",
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "UNKNOWN_COLUMN"


def test_handler_cannot_see_or_change_the_registry(client, as_user):
    push_schema(client)
    handler_client = as_user("ops_park")
    assert handler_client.get("/api/protection").status_code == 403
    assert (
        register(
            handler_client,
            table_name="member",
            column_name="rrn_enc",
            item="UNIQUE_ID",
            data_category="MEMBER_BASIC",
        ).status_code
        == 403
    )
    assert handler_client.get("/api/protection/history").status_code == 403


@pytest.mark.parametrize(
    "statement",
    ["UPDATE protected_column_history SET reason = '고침'", "DELETE FROM protected_column_history"],
)
def test_history_is_append_only(admin_engine, app_engine, statement):
    with pytest.raises(DBAPIError, match="append-only"):
        with admin_engine.begin() as conn:
            conn.execute(text(statement))
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text(statement))


def test_registry_screens_stay_out_of_argus_self_log(client, as_user, app_engine):
    push_schema(client)
    officer = as_user("officer")
    officer.get("/api/protection")
    officer.get("/api/protection/history")
    with app_engine.connect() as conn:
        paths = list(conn.execute(text("SELECT request_path FROM access_log")).scalars())
    assert not [p for p in paths if p and p.startswith("/api/protection")]


# ── N-3 게이트웨이에 내려주는 판정 표 ─────────────────────


def test_table_category_is_the_most_sensitive_registered_column(client, as_user):
    push_schema(client)
    officer = as_user("officer")
    # 회원 테이블에 결제수단(계좌) 컬럼을 등록하면 테이블 전체가 PAYMENT로 판정된다
    register(
        officer, table_name="member", column_name="rrn_enc", item="ACCOUNT", data_category="PAYMENT"
    )
    registry = fetch_registry(client).json()
    assert registry["tables"]["member"] == "PAYMENT"
    assert "operator" not in registry["tables"]


def test_registry_endpoint_needs_signature_and_has_no_values(client):
    assert fetch_registry(client, secret="wrong-secret-0000000000000000").status_code == 401
    body = fetch_registry(client).json()
    assert set(body) == {"version", "database", "tables", "member_columns"}


def test_released_member_key_leaves_the_registry(client, as_user, app_engine):
    with app_engine.connect() as conn:
        column_id = conn.execute(
            select(protected_column.c.id).where(
                protected_column.c.table_name == "inquiry",
                protected_column.c.column_name == "member_id",
            )
        ).scalar_one()
    as_user("officer").post(
        f"/api/protection/columns/{column_id}/release", json={"reason": "테스트"}
    )
    registry = fetch_registry(client).json()
    assert ["inquiry", "member_id"] not in registry["member_columns"]
    assert registry["tables"]["inquiry"] == "INQUIRY"  # 다른 개인정보 컬럼(제목·본문)이 남아 있다


# ── 원장 context.registry_version (게이트웨이가 보냄) ──────


def test_ledger_accepts_registry_version_for_db_records(client):
    from conftest import make_event, post_events

    context = {
        "db_user": "platform_owner",
        "sql_normalized": "SELECT * FROM member WHERE id = $1",
        "tables": ["member"],
        "row_count": 1,
        "raw_ref": "6f1d0c2e-0000-4000-8000-0000000000aa",
        "raw_fingerprint": "sha256:" + "ab" * 32,
        "subject_unresolved": True,
        "registry_version": "r12",
    }
    ok = make_event(access_path="DB", action="READ", context=context)
    bad = make_event(access_path="DB", action="READ", context=context | {"registry_version": "x"})
    res = post_events(client, [ok, bad]).json()
    assert res["accepted"] == 1 and res["rejected"][0]["code"] == "INVALID_FIELD"


# ── N-4 현황 — 테이블별 최근 30일 DB 직접 접근 ────────────


def test_overview_counts_recent_db_access_per_table(client, as_user, app_engine):
    push_schema(client)
    context = {
        "db_user": "platform_owner",
        "sql_normalized": "SELECT * FROM public.member",
        "tables": ["public.member"],
        "row_count": 3,
        "raw_ref": "6f1d0c2e-0000-4000-8000-0000000000ab",
        "raw_fingerprint": "sha256:" + "cd" * 32,
        "subject_unresolved": True,
    }
    with app_engine.begin() as conn:
        append_access_logs(
            conn,
            [
                make_entry(access_path="DB", subject_ids=[], subject_count=3, context=context),
                make_entry(access_path="DB", subject_ids=[], subject_count=3, context=context),
                make_entry(  # 31일 전 — 세지 않는다
                    access_path="DB",
                    subject_ids=[],
                    subject_count=3,
                    context=context,
                    occurred_at=datetime.now(UTC) - timedelta(days=31),
                ),
            ],
            received_at=datetime.now(UTC),
        )
    member = next(
        t
        for t in as_user("officer").get("/api/protection").json()["tables"]
        if t["table_name"] == "member"
    )
    assert member["db_access_30d"] == 2 and member["last_db_access_at"] is not None
