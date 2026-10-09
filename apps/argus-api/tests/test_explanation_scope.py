"""소명 단위 보완 (v0.1 보강 J — 개선점 통합검토 4-5)

J-1 소명 제출 뒤에 같은 탐지건에 붙은 기록은 "그 소명이 다루지 않은 행위"로 구분해 보인다
     (목록·상세·보고서). 승인은 막지 않는다(확인 창은 화면)
J-2 EVENT 룰은 데이터 유형·행위 구분이 다르면 다른 탐지건. AGGREGATE는 그대로
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.detection.batch import action_group, run_batch
from app.ledger.append import append_access_logs
from app.models import argus_user, detection, detection_log, detection_rule, handler

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

PASSWORD = "scope-test-password-1"  # 테스트 전용 더미 값
KST = timezone(timedelta(hours=9))
NIGHT = datetime(2026, 10, 7, 23, 10, tzinfo=KST)  # 야간 접속 룰에 걸리는 고정 시각(수요일)


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
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
    with admin_engine.begin() as conn:
        for table in (
            "inspection_report",
            "notification",
            "detection_log",
            "detection_status_history",
            "explanation",
            "detection",
            "detection_batch_run",
            "argus_user",
            "handler",
        ):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))


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


def append(app_engine, *entries: dict) -> list[int]:
    with app_engine.begin() as conn:
        return append_access_logs(
            conn, [make_entry(**e) for e in entries], received_at=datetime.now(UTC)
        ).inserted_ids


def download(count: int = 120, start: int = 10001, **overrides) -> dict:
    return {
        "action": "DOWNLOAD",
        "subject_ids": [str(n) for n in range(start, start + count)],
        "subject_count": count,
        "request_path": "/admin/members/export",
        **overrides,
    }


def cases(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        return [
            dict(r) for r in conn.execute(select(detection).order_by(detection.c.id)).mappings()
        ]


def linked(app_engine, case: int) -> list[int]:
    with app_engine.connect() as conn:
        return sorted(
            conn.execute(
                select(detection_log.c.access_log_id).where(detection_log.c.detection_id == case)
            ).scalars()
        )


# ── J-1 제출 뒤에 붙은 기록 ───────────────────────────────


@pytest.fixture
def submitted_case(app_engine, as_user) -> int:
    """대량 다운로드 → 자동 요청 → 취급자 제출 (1차)"""
    append(app_engine, download())
    run_batch(app_engine)
    [case] = cases(app_engine)
    res = as_user("ops_park").post(
        f"/api/detections/{case['id']}/submit", json={"content": "월말 정산 자료 요청"}
    )
    assert res.status_code == 200
    return case["id"]


def test_log_attached_after_submission_is_marked(app_engine, as_user, submitted_case):
    [late] = append(app_engine, download(count=80, start=20001))
    run_batch(app_engine)
    assert len(linked(app_engine, submitted_case)) == 2  # 같은 그룹이라 같은 탐지건에 붙는다

    officer = as_user("officer")
    detail = officer.get(f"/api/detections/{submitted_case}").json()
    assert detail["after_submission_count"] == 1
    marks = {log["access_log_id"]: log["after_submission_round"] for log in detail["logs"]}
    assert marks[late] == 1
    assert [v for k, v in marks.items() if k != late] == [None]

    listed = officer.post("/api/detections/search", json={}).json()["items"]
    assert listed[0]["after_submission_count"] == 1


def test_approval_is_not_blocked(app_engine, as_user, submitted_case):
    append(app_engine, download(count=80, start=20001))
    run_batch(app_engine)
    res = as_user("officer").post(f"/api/detections/{submitted_case}/approve", json={})
    assert res.status_code == 200 and res.json()["status"] == "APPROVED"


def test_resubmission_covers_the_late_log(app_engine, as_user, submitted_case):
    append(app_engine, download(count=80, start=20001))
    run_batch(app_engine)
    officer, handler_client = as_user("officer"), as_user("ops_park")
    officer.post(f"/api/detections/{submitted_case}/reject", json={"comment": "추가 기록 소명"})
    officer.post(f"/api/detections/{submitted_case}/request", json={})

    waiting = officer.get(f"/api/detections/{submitted_case}").json()
    assert waiting["after_submission_count"] == 0  # 2차 제출 전 — 다음 소명이 다룬다

    handler_client.post(
        f"/api/detections/{submitted_case}/submit", json={"content": "두 번 모두 정산 자료"}
    )
    detail = officer.get(f"/api/detections/{submitted_case}").json()
    assert detail["after_submission_count"] == 0
    assert all(log["after_submission_round"] is None for log in detail["logs"])


def test_no_late_log_means_zero(as_user, submitted_case):
    detail = as_user("officer").get(f"/api/detections/{submitted_case}").json()
    assert detail["after_submission_count"] == 0


def test_report_case_carries_after_submission_count(app_engine, as_user, submitted_case):
    append(app_engine, download(count=80, start=20001))
    run_batch(app_engine)
    today = datetime.now(KST).date().isoformat()
    res = as_user("officer").post(
        "/api/reports", json={"date_from": today, "date_to": today, "scope": "APP"}
    )
    [case] = res.json()["summary"]["paths"]["APP"]["cases"]
    assert case["after_submission"] == 1


# ── J-2 다른 성격의 처리는 다른 탐지건 ────────────────────


def night(**overrides) -> dict:
    return {"occurred_at": NIGHT, **overrides}


@pytest.fixture
def night_rule(admin_engine, seed_rules):
    reset_rules(admin_engine, seed_rules, enabled=("야간 접속",))


def test_action_groups():
    assert action_group("READ") == "READ"
    assert action_group("DOWNLOAD") == action_group("EXPORT") == "DOWNLOAD"
    assert action_group("CREATE") == action_group("UPDATE") == action_group("DELETE") == "CHANGE"
    assert action_group("LOGIN") == action_group("LOGOUT") == "SESSION"


def test_different_data_category_is_a_new_case(app_engine, night_rule):
    # 같은 날 야간에 회원정보 조회 후 결제정보 조회 → 탐지건 2개
    append(
        app_engine,
        night(data_category="MEMBER_BASIC"),
        night(data_category="PAYMENT", request_path="/admin/members/10293/payment"),
    )
    run_batch(app_engine)
    found = cases(app_engine)
    assert [(c["data_category"], c["action_group"]) for c in found] == [
        ("MEMBER_BASIC", "READ"),
        ("PAYMENT", "READ"),
    ]
    assert len({c["group_bucket"] for c in found}) == 1  # 같은 날짜 — 성격만 다르다


def test_different_action_group_is_a_new_case(app_engine, night_rule):
    # 회원정보 조회 후 회원정보 다운로드 → 2개
    append(app_engine, night(), night(**download(count=3)))
    run_batch(app_engine)
    assert [c["action_group"] for c in cases(app_engine)] == ["READ", "DOWNLOAD"]


def test_same_category_and_action_group_share_a_case(app_engine, night_rule):
    ids = append(app_engine, night(), night(subject_ids=["10294"]))
    run_batch(app_engine)
    [case] = cases(app_engine)
    assert linked(app_engine, case["id"]) == sorted(ids)


def test_change_actions_share_one_case(app_engine, admin_engine, seed_rules):
    # 등록·수정·삭제는 같은 "변경·삭제" 구분 — 한 탐지건
    reset_rules(admin_engine, seed_rules, enabled=())
    with admin_engine.begin() as conn:
        conn.execute(
            insert(detection_rule).values(
                name="테스트 — 회원정보 변경",
                rule_type="EVENT",
                access_path="APP",
                severity="LOW",
                condition={"all": [{"field": "action", "op": "in", "value": ["CREATE", "UPDATE"]}]},
                auto_request=False,
            )
        )
    ids = append(app_engine, {"action": "CREATE"}, {"action": "UPDATE"})
    run_batch(app_engine)
    [case] = cases(app_engine)
    assert case["action_group"] == "CHANGE"
    assert linked(app_engine, case["id"]) == sorted(ids)


def test_aggregate_rule_is_not_split(app_engine, admin_engine, seed_rules):
    # 대량 조회(1시간 조회 100건) — 데이터 유형이 섞여도 한 집계·한 탐지건, 성격 칸은 비움
    reset_rules(admin_engine, seed_rules, enabled=("대량 조회",))
    hour = datetime(2026, 10, 7, 14, 0, tzinfo=KST)
    entries = [
        {
            "occurred_at": hour + timedelta(seconds=n),
            "data_category": "MEMBER_BASIC" if n % 2 else "ORDER",
        }
        for n in range(100)
    ]
    append(app_engine, *entries)
    run_batch(app_engine)
    [case] = cases(app_engine)
    assert (case["data_category"], case["action_group"]) == (None, None)
    assert case["log_count"] == 100
