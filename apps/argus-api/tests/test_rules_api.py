"""룰 빌더 API — 목록·필터, 생성·수정·켜기/끄기, 변경 이력, 동시 수정, 권한 (기능 레이어 6)"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.detection.batch import run_batch
from app.ledger.append import append_access_logs
from app.models import access_log, argus_user, detection, handler

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
SEED_NAMES = (
    "대량 다운로드",
    "야간 접속",
    "주말 접속",
    "퇴직자 계정 접속",
    "대량 조회",
    "전월 대비 급증",
    "결제수단 조회",  # 기능 레이어 7 ①
    # 기능 레이어 8 ③ — DB 직접 접근(2티어) 기본 룰
    "DB 직접 야간 접근",
    "DB 직접 주말 접근",
    "DB 직접 전월 대비 급증",
    # v0.1 보강 K-2 — 접속지·특정 회원 기반
    "허용 범위 밖 접속지",
    "짧은 시간 여러 접속지",
    "특정 회원 반복 처리",
)

# 쪼개기 다운로드 — 대량 다운로드(50명)를 피해 여러 번 나눠 받는 우회를 잡는 룰 (생성 예시)
SPLIT_DOWNLOAD = {
    "name": "쪼개기 다운로드",
    "description": "하루 다운로드 처리 건수 합 200명 이상",
    "rule_type": "AGGREGATE",
    "severity": "HIGH",
    "condition": {"all": [{"field": "action", "op": "eq", "value": "DOWNLOAD"}]},
    "aggregate": {
        "window": "1d",
        "measure": "SUBJECT_COUNT",
        "compare": "ABSOLUTE",
        "threshold": 200,
    },
}


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
    """담당자 officer·officer2, 취급자 ops_park (가상 인물). 룰은 시드 상태(모두 켜짐)"""
    reset_rules(admin_engine, seed_rules, enabled=SEED_NAMES)
    with admin_engine.begin() as conn:
        for login_id in ("officer", "officer2"):
            conn.execute(
                insert(argus_user).values(
                    login_id=login_id, password_hash=password_hash, role="OFFICER"
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
        ):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름
    reset_rules(admin_engine, seed_rules, enabled=SEED_NAMES)
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))


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


def rule_by_name(client, name: str) -> dict:
    [rule] = [r for r in client.get("/api/rules").json()["items"] if r["name"] == name]
    return rule


def editable(rule: dict, **changes) -> dict:
    """화면이 수정 저장 때 보내는 본문 — 현재 값 + 바꾼 값 + 본 버전"""
    keys = ("name", "description", "severity", "access_path", "auto_request", "condition")
    body = {k: rule[k] for k in keys} | {"aggregate": rule["aggregate"]}
    return body | changes | {"expected_version": rule["version"]}


# ── 목록·상세 ─────────────────────────────────────────────


def test_list_has_seed_rules_and_filters_by_enabled(as_user):
    officer = as_user("officer")
    items = officer.get("/api/rules").json()["items"]
    assert [r["name"] for r in items] == list(SEED_NAMES)
    assert all(r["updated_by"] is None and r["detection_count"] == 0 for r in items)

    night = rule_by_name(officer, "야간 접속")
    officer.post(f"/api/rules/{night['id']}/disable", json={"expected_version": night["version"]})

    off = officer.get("/api/rules", params={"enabled": "false"}).json()["items"]
    on = officer.get("/api/rules", params={"enabled": "true"}).json()["items"]
    assert [r["name"] for r in off] == ["야간 접속"]
    assert len(on) == len(SEED_NAMES) - 1


def test_list_filters_by_name_path_severity_and_type(as_user):
    # v0.1 보강 C-3 — 룰 이름은 개인정보가 아니라 조회 조건을 URL 쿼리로 받는다
    officer = as_user("officer")

    def names(**params) -> list[str]:
        res = officer.get("/api/rules", params=params)
        assert res.status_code == 200, res.text
        return [r["name"] for r in res.json()["items"]]

    assert names(name="야간") == ["야간 접속", "DB 직접 야간 접근"]
    assert names(name="DB", access_path="DB", severity="HIGH") == ["DB 직접 야간 접근"]
    assert names(rule_type="AGGREGATE", access_path="APP") == [
        "대량 조회",
        "전월 대비 급증",
        "특정 회원 반복 처리",
    ]
    assert names(name="없는 룰") == []
    for bad in ({"severity": "CRITICAL"}, {"rule_type": "X"}, {"access_path": "WEB"}):
        assert officer.get("/api/rules", params=bad).status_code == 400


def test_detail_includes_history_newest_first(as_user):
    officer = as_user("officer")
    bulk = rule_by_name(officer, "대량 다운로드")
    officer.put(f"/api/rules/{bulk['id']}", json=editable(bulk, severity="MEDIUM"))

    detail = officer.get(f"/api/rules/{bulk['id']}").json()
    assert [(h["version"], h["change_type"], h["changed_by"]) for h in detail["history"]] == [
        (2, "UPDATE", "officer"),
        (1, "CREATE", None),  # 마이그레이션 시드 — 시스템
    ]
    assert detail["history"][0]["snapshot"]["severity"] == "MEDIUM"
    assert detail["updated_by"] == "officer"


def test_unknown_rule_is_404(as_user):
    assert as_user("officer").get("/api/rules/999999").status_code == 404


# ── 생성 ──────────────────────────────────────────────────


def test_create_rule(as_user):
    officer = as_user("officer")
    response = officer.post("/api/rules", json=SPLIT_DOWNLOAD)

    assert response.status_code == 201
    created = response.json()
    assert created["version"] == 1 and created["enabled"] is True
    assert created["aggregate"]["threshold"] == 200
    detail = officer.get(f"/api/rules/{created['id']}").json()
    assert [(h["change_type"], h["changed_by"]) for h in detail["history"]] == [
        ("CREATE", "officer")
    ]


@pytest.mark.parametrize(
    "body",
    [
        SPLIT_DOWNLOAD | {"condition": {"all": [{"field": "mood", "op": "eq", "value": "x"}]}},
        SPLIT_DOWNLOAD | {"aggregate": None},  # 집계 룰인데 집계 스펙 없음
        SPLIT_DOWNLOAD | {"rule_type": "EVENT"},  # 단건 룰에 집계 스펙
        SPLIT_DOWNLOAD | {"aggregate": SPLIT_DOWNLOAD["aggregate"] | {"threshold": 0}},
    ],
    ids=["unknown-field", "missing-aggregate", "event-with-aggregate", "zero-threshold"],
)
def test_unevaluable_rule_is_rejected_on_save(as_user, body):
    # 저장 단계에서 막는다 — 켜진 룰 하나가 순찰 전체를 멈추지 않게 (db-schema 3-7 #2)
    response = as_user("officer").post("/api/rules", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RULE"


def test_duplicate_name_is_409(as_user):
    response = as_user("officer").post("/api/rules", json=SPLIT_DOWNLOAD | {"name": "대량 조회"})
    assert response.status_code == 409


# ── 수정 ──────────────────────────────────────────────────


def test_update_threshold_bumps_version_and_records_history(as_user):
    officer = as_user("officer")
    bulk_read = rule_by_name(officer, "대량 조회")
    body = editable(bulk_read, aggregate=bulk_read["aggregate"] | {"threshold": 50})

    updated = officer.put(f"/api/rules/{bulk_read['id']}", json=body).json()

    assert updated["version"] == bulk_read["version"] + 1
    assert updated["aggregate"]["threshold"] == 50
    assert updated["rule_type"] == "AGGREGATE"


def test_stale_version_is_409(as_user):
    # 두 담당자가 같은 화면을 열고 차례로 저장 — 뒤의 사람은 덮어쓰지 못한다
    first, second = as_user("officer"), as_user("officer2")
    seen = rule_by_name(first, "야간 접속")
    assert first.put(f"/api/rules/{seen['id']}", json=editable(seen, severity="HIGH")).is_success

    response = second.put(f"/api/rules/{seen['id']}", json=editable(seen, severity="LOW"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"


def test_rule_type_cannot_change(as_user):
    officer = as_user("officer")
    night = rule_by_name(officer, "야간 접속")  # EVENT
    body = editable(night, aggregate=SPLIT_DOWNLOAD["aggregate"])
    assert officer.put(f"/api/rules/{night['id']}", json=body).status_code == 400


def test_unchanged_save_does_not_bump_version(as_user):
    officer = as_user("officer")
    weekend = rule_by_name(officer, "주말 접속")
    saved = officer.put(f"/api/rules/{weekend['id']}", json=editable(weekend)).json()
    assert saved["version"] == weekend["version"]
    assert len(officer.get(f"/api/rules/{weekend['id']}").json()["history"]) == 1


# ── 켜기·끄기 ─────────────────────────────────────────────


def test_disable_and_enable_are_recorded(as_user):
    officer = as_user("officer")
    rule = rule_by_name(officer, "주말 접속")
    off = officer.post(
        f"/api/rules/{rule['id']}/disable", json={"expected_version": rule["version"]}
    )
    assert off.json()["enabled"] is False
    again = officer.post(
        f"/api/rules/{rule['id']}/disable", json={"expected_version": off.json()["version"]}
    )
    assert again.json()["version"] == off.json()["version"]  # 이미 꺼짐 — 변화 없음
    on = officer.post(
        f"/api/rules/{rule['id']}/enable", json={"expected_version": off.json()["version"]}
    )
    assert on.json()["enabled"] is True

    history = officer.get(f"/api/rules/{rule['id']}").json()["history"]
    assert [h["change_type"] for h in history] == ["ENABLE", "DISABLE", "CREATE"]


# ── 권한·접속기록 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/rules", None),
        ("get", "/api/rules/1", None),
        ("post", "/api/rules", SPLIT_DOWNLOAD),
        ("put", "/api/rules/1", SPLIT_DOWNLOAD | {"expected_version": 1}),
        ("post", "/api/rules/1/disable", {"expected_version": 1}),
    ],
)
def test_handler_cannot_manage_rules(as_user, method, path, body):
    handler_client = as_user("ops_park")
    kwargs = {"json": body} if body is not None else {}
    assert getattr(handler_client, method)(path, **kwargs).status_code == 403


def test_rule_pages_are_not_self_access_logged(app_engine, as_user):
    # 룰 정의에는 정보주체가 없다 — 변경은 룰 변경 이력에 남는다
    officer = as_user("officer")
    officer.get("/api/rules")
    with app_engine.connect() as conn:
        paths = set(conn.execute(select(access_log.c.request_path)).scalars())
    assert not any(p and p.startswith("/api/rules") for p in paths)


def test_unauthenticated_is_401(client):
    assert client.get("/api/rules").status_code == 401


# ── 탐지 배치에 반영 ──────────────────────────────────────


def test_next_batch_uses_the_updated_rule(app_engine, admin_engine, seed_rules, as_user):
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    officer = as_user("officer")
    bulk = rule_by_name(officer, "대량 다운로드")
    lowered = {
        "all": [
            {"field": "action", "op": "eq", "value": "DOWNLOAD"},
            {"field": "subject_count", "op": "gte", "value": 30},
        ]
    }
    officer.put(f"/api/rules/{bulk['id']}", json=editable(bulk, condition=lowered))

    with app_engine.begin() as conn:
        append_access_logs(
            conn,
            [
                make_entry(
                    action="DOWNLOAD",
                    subject_ids=[str(n) for n in range(10001, 10041)],
                    subject_count=40,
                )
            ],
            received_at=datetime.now(UTC),
        )
    run_batch(app_engine)

    with app_engine.connect() as conn:
        [case] = conn.execute(select(detection)).mappings().all()
    assert case["rule_version"] == bulk["version"] + 1  # 탐지 당시 룰 버전·사본이 남는다
    assert case["rule_snapshot"]["condition"] == lowered
