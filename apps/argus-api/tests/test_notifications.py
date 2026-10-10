"""알림 — 누가 무엇을 받는가, 소명 기한, 화면 알림 API (v0.1 보강 F-1~F-3)"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text, update

from app.auth.passwords import hash_password
from app.detection.batch import run_batch
from app.ledger.append import append_access_logs
from app.models import (
    access_log,
    argus_user,
    detection,
    detection_rule,
    explanation,
    handler,
    notification,
    setting,
)
from app.notifications.events import check_due

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
NOTIFICATIONS = "/api/notifications"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
    """담당자 officer·officer2, 취급자 ops_park·mkt_lee (가상 인물). 룰은 대량 다운로드만"""
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    with admin_engine.begin() as conn:
        for login_id in ("officer", "officer2"):
            conn.execute(
                insert(argus_user).values(
                    login_id=login_id, password_hash=password_hash, role="OFFICER"
                )
            )
        for login_id, name in (("ops_park", "박지훈"), ("mkt_lee", "이수민")):
            handler_id = conn.execute(
                insert(handler)
                .values(
                    source_system_id=1,
                    login_id=login_id,
                    name=name,
                    team="OPS",
                    employment_status="ACTIVE",
                    last_event_at=datetime.now(UTC),
                )
                .returning(handler.c.id)
            ).scalar_one()
            conn.execute(
                insert(argus_user).values(
                    login_id=login_id,
                    password_hash=password_hash,
                    role="HANDLER",
                    handler_id=handler_id,
                )
            )
    yield
    with admin_engine.begin() as conn:
        for table in (
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
        conn.execute(text("UPDATE setting SET value = '7' WHERE key = 'explanation_due_days'"))


def detect(app_engine, actor="ops_park") -> int:
    with app_engine.begin() as conn:
        append_access_logs(
            conn,
            [
                make_entry(
                    actor_login_id=actor,
                    action="DOWNLOAD",
                    subject_ids=[str(n) for n in range(10001, 10121)],
                    subject_count=120,
                    request_path="/admin/members/export",
                )
            ],
            received_at=datetime.now(UTC),
        )
    run_batch(app_engine)
    with app_engine.connect() as conn:
        return conn.execute(
            select(detection.c.id)
            .where(detection.c.actor_login_id == actor)
            .order_by(detection.c.id.desc())
        ).scalar()


def received(app_engine, case_id: int | None = None) -> dict[str, list[tuple[str, int]]]:
    """사용자 → [(종류, 차수)]"""
    query = (
        select(argus_user.c.login_id, notification.c.kind, notification.c.round)
        .join(argus_user, argus_user.c.id == notification.c.user_id)
        .order_by(notification.c.id)
    )
    if case_id is not None:
        query = query.where(notification.c.detection_id == case_id)
    result: dict[str, list[tuple[str, int]]] = {}
    with app_engine.connect() as conn:
        for login_id, kind, round_ in conn.execute(query):
            result.setdefault(login_id, []).append((kind, round_))
    return result


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


def act(client, case_id, action, **body):
    res = client.post(f"/api/detections/{case_id}/{action}", json=body)
    assert res.status_code == 200, res.text
    return res


def _set_severity(admin_engine, severity: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            update(detection_rule)
            .where(detection_rule.c.name == "대량 다운로드")
            .values(severity=severity)
        )


# ── 누가 받는가 (F-1) ─────────────────────────────────────


def test_high_detection_notifies_all_officers_and_requests_the_handler(app_engine):
    case = detect(app_engine)
    assert received(app_engine, case) == {
        "officer": [("DETECTED", 0)],
        "officer2": [("DETECTED", 0)],
        "ops_park": [("REQUESTED", 1)],  # 자동 소명 요청
    }


@pytest.mark.parametrize("severity", ["MEDIUM", "LOW"])
def test_medium_and_low_detection_only_request_the_handler(app_engine, admin_engine, severity):
    _set_severity(admin_engine, severity)
    case = detect(app_engine)
    # 중·하 탐지건은 담당자 목록에서만 — 취급자에게는 할 일이 생겼으니 알린다
    assert received(app_engine, case) == {"ops_park": [("REQUESTED", 1)]}


def test_case_without_handler_account_notifies_officers_only(app_engine):
    case = detect(app_engine, actor="cs_kim")  # Argus 계정 없음 → DETECTED로 남음
    assert received(app_engine, case) == {
        "officer": [("DETECTED", 0)],
        "officer2": [("DETECTED", 0)],
    }


def test_submission_notifies_the_requester_or_every_officer(app_engine, as_user):
    case = detect(app_engine)
    handler_client, officer = as_user("ops_park"), as_user("officer")

    act(handler_client, case, "submit", content="1차 소명")  # 1차는 시스템 자동 요청
    assert received(app_engine, case)["officer2"][-1] == ("SUBMITTED", 1)
    assert received(app_engine, case)["officer"][-1] == ("SUBMITTED", 1)

    act(officer, case, "reject", comment="근거 부족")
    act(officer, case, "request", message="티켓 번호를 적어 주세요")  # 2차는 officer가 요청
    assert received(app_engine, case)["ops_park"][-1] == ("REQUESTED", 2)
    act(handler_client, case, "submit", content="2차 소명")
    got = received(app_engine, case)
    assert got["officer"][-1] == ("SUBMITTED", 2)
    assert ("SUBMITTED", 2) not in got["officer2"]  # 요청한 담당자에게만


# ── 소명 기한 (F-3) ───────────────────────────────────────


def _due(app_engine, case_id: int, round_: int = 1) -> tuple[datetime, datetime]:
    with app_engine.connect() as conn:
        return conn.execute(
            select(explanation.c.requested_at, explanation.c.due_at).where(
                explanation.c.detection_id == case_id, explanation.c.round == round_
            )
        ).one()


def test_due_is_seven_days_after_request_and_resets_on_re_request(app_engine, as_user):
    case = detect(app_engine)
    requested, due = _due(app_engine, case)
    assert due - requested == pytest.approx(timedelta(days=7), abs=timedelta(seconds=5))

    officer = as_user("officer")
    act(as_user("ops_park"), case, "submit", content="소명")
    act(officer, case, "reject", comment="반려")
    act(officer, case, "request")
    requested2, due2 = _due(app_engine, case, 2)
    assert due2 - requested2 == pytest.approx(timedelta(days=7), abs=timedelta(seconds=5))


def test_due_days_follow_the_setting(app_engine, admin_engine):
    with admin_engine.begin() as conn:
        conn.execute(update(setting).where(setting.c.key == "explanation_due_days").values(value=3))
    requested, due = _due(app_engine, detect(app_engine))
    assert due - requested == pytest.approx(timedelta(days=3), abs=timedelta(seconds=5))


def test_due_soon_then_overdue_each_once(app_engine):
    case = detect(app_engine)
    _, due = _due(app_engine, case)

    assert check_due(app_engine, now=due - timedelta(days=2)) == 0  # 아직 멀다
    assert check_due(app_engine, now=due - timedelta(hours=12)) == 3  # 취급자 + 담당자 2명
    assert check_due(app_engine, now=due - timedelta(hours=1)) == 0  # 한 번만
    assert check_due(app_engine, now=due + timedelta(hours=1)) == 3
    assert check_due(app_engine, now=due + timedelta(days=3)) == 0
    got = received(app_engine, case)
    for who in ("ops_park", "officer", "officer2"):
        assert [k for k, _ in got[who] if k in ("DUE_SOON", "OVERDUE")] == ["DUE_SOON", "OVERDUE"]
    with app_engine.connect() as conn:  # 기한을 넘겨도 상태는 그대로 (담당자 판단)
        assert conn.execute(select(detection.c.status).where(detection.c.id == case)).scalar() == (
            "REQUESTED"
        )


def test_first_seen_after_due_is_only_overdue(app_engine):
    case = detect(app_engine)
    _, due = _due(app_engine, case)
    check_due(app_engine, now=due + timedelta(days=1))
    assert [k for k, _ in received(app_engine, case)["ops_park"]] == ["REQUESTED", "OVERDUE"]


def test_submitted_or_closed_requests_are_not_chased(app_engine, as_user):
    submitted = detect(app_engine)
    dismissed = detect(app_engine, actor="mkt_lee")
    act(as_user("ops_park"), submitted, "submit", content="소명")
    act(as_user("officer"), dismissed, "dismiss", reason="오탐")
    _, due = _due(app_engine, submitted)
    assert check_due(app_engine, now=due + timedelta(days=1)) == 0


def test_case_list_and_detail_show_the_due(app_engine, as_user):
    case = detect(app_engine)
    _, due = _due(app_engine, case)
    officer = as_user("officer")
    [item] = officer.post("/api/detections/search", json={}).json()["items"]
    assert datetime.fromisoformat(item["due_at"]) == due
    [round1] = officer.get(f"/api/detections/{case}").json()["explanations"]
    assert datetime.fromisoformat(round1["due_at"]) == due


# ── 화면 알림 API (F-2) ───────────────────────────────────


def test_api_shows_own_notifications_without_personal_data(app_engine, as_user):
    case = detect(app_engine)
    detect(app_engine, actor="mkt_lee")  # 남의 소명 요청
    res = as_user("ops_park").get(NOTIFICATIONS)
    assert res.status_code == 200
    body = res.json()
    assert body["unread"] == 1
    [item] = body["items"]
    assert set(item) == {
        "id",
        "kind",
        "detection_id",
        "severity",
        "round",
        "created_at",
        "read_at",
        "rule_name",
    }
    assert (item["kind"], item["detection_id"], item["rule_name"]) == (
        "REQUESTED",
        case,
        "대량 다운로드",
    )
    # 회원번호·취급자 이름은 알림에 없다
    assert "10001" not in res.text and "박지훈" not in res.text and "이수민" not in res.text


def test_read_one_and_read_all(app_engine, as_user):
    detect(app_engine)
    detect(app_engine, actor="mkt_lee")
    officer = as_user("officer")
    items = officer.get(NOTIFICATIONS).json()["items"]
    assert len(items) == 2
    assert officer.post(f"{NOTIFICATIONS}/{items[0]['id']}/read").status_code == 204
    assert officer.get(NOTIFICATIONS).json()["unread"] == 1
    assert officer.post(f"{NOTIFICATIONS}/read-all").status_code == 204
    assert officer.get(NOTIFICATIONS).json()["unread"] == 0


def test_cannot_mark_someone_elses_notification(app_engine, as_user):
    detect(app_engine)
    [mine] = as_user("ops_park").get(NOTIFICATIONS).json()["items"]
    assert as_user("mkt_lee").post(f"{NOTIFICATIONS}/{mine['id']}/read").status_code == 404
    assert as_user("ops_park").get(NOTIFICATIONS).json()["unread"] == 1


def test_polling_does_not_fill_the_ledger(app_engine, as_user):
    officer = as_user("officer")
    for _ in range(3):
        officer.get(NOTIFICATIONS)
    with app_engine.connect() as conn:
        paths = conn.execute(
            select(access_log.c.request_path).where(access_log.c.actor_login_id == "officer")
        ).scalars()
        assert not any(p and p.startswith(NOTIFICATIONS) for p in paths)


def test_unauthenticated_is_401(client):
    assert client.get(NOTIFICATIONS).status_code == 401


def test_notification_rows_hold_no_text(app_engine):
    detect(app_engine)
    with app_engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(select(notification)).mappings()]
    columns = {"id", "user_id", "kind", "detection_id", "severity", "round", "created_at"}
    columns |= {"read_at", "push_pending"}  # push_pending: 웹 푸시 발송 대기 (F-4)
    assert rows and all(set(r) == columns for r in rows)
    # 상 탐지건(담당자)·상 소명 요청(취급자)은 웹 푸시도 — 급한 건
    assert all(r["push_pending"] for r in rows)
    assert "10001" not in json.dumps(rows, default=str)
