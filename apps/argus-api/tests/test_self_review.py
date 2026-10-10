"""본인 건 처리 차단 (v0.1 보강 H — 갭 A18, 고시 §8② 점검의 객관성)

담당자가 탐지건의 행위자 본인이면 그 건의 상태를 바꿀 수 없다(403 SELF_REVIEW_FORBIDDEN).
- 본인 = 담당자 계정에 연결된 명부의 (출처, 계정)이 탐지건 행위자와 같음
- 연결이 없으면 담당자 로그인 아이디 = 탐지건 행위자 아이디(출처 PLATFORM)
- 거부된 시도는 상태 이력에 남지 않고, Argus 자체 접속기록에 FAILURE로 남는다
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text, update
from test_detections_api import PASSWORD, act, detect, status_of

from app.auth.passwords import hash_password
from app.models import access_log, argus_user, detection, detection_status_history, handler
from app.models import source_system as source_system_table

from conftest import TEST_CLIENT_ADDR, reset_rules


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
    """담당자 3명(가상 인물)
    - officer: 명부 연결 없음, 플랫폼 계정도 없음 — 다른 담당자
    - kim_sec: 플랫폼 계정 sec_kim(명부)에 연결된 담당자 — 아이디가 달라도 연결로 본인 판정
    - ops_choi: 연결 없음, 플랫폼에서도 같은 아이디로 일함 — 아이디로 본인 판정
    취급자 ops_park(비교용)
    """
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    with admin_engine.begin() as conn:

        def add_handler(login_id: str, name: str) -> int:
            return conn.execute(
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
                login_id="officer", password_hash=password_hash, role="OFFICER"
            )
        )
        conn.execute(
            insert(argus_user).values(
                login_id="kim_sec",
                password_hash=password_hash,
                role="OFFICER",
                handler_id=add_handler("sec_kim", "김보안"),
            )
        )
        conn.execute(
            insert(argus_user).values(
                login_id="ops_choi", password_hash=password_hash, role="OFFICER"
            )
        )
        conn.execute(
            insert(argus_user).values(
                login_id="ops_park",
                password_hash=password_hash,
                role="HANDLER",
                handler_id=add_handler("ops_park", "박지훈"),
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


def set_status(admin_engine, case: int, status: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(update(detection).where(detection.c.id == case).values(status=status, round=1))


def history_count(app_engine, case: int) -> int:
    with app_engine.connect() as conn:
        return len(
            conn.execute(
                select(detection_status_history.c.id).where(
                    detection_status_history.c.detection_id == case
                )
            ).all()
        )


# 담당자의 모든 상태 전이 — (전이 전 상태, 행동, 본문)
OFFICER_TRANSITIONS = [
    ("DETECTED", "request", {}),
    ("DETECTED", "dismiss", {"reason": "오탐"}),
    ("REQUESTED", "dismiss", {"reason": "요청 취소"}),
    ("SUBMITTED", "approve", {}),
    ("SUBMITTED", "reject", {"comment": "근거 부족"}),
    ("REJECTED", "request", {"message": "재요청"}),
    ("REJECTED", "escalate", {}),
]


@pytest.mark.parametrize(("status", "action", "body"), OFFICER_TRANSITIONS)
def test_linked_officer_cannot_process_own_case(
    admin_engine, app_engine, as_user, status, action, body
):
    case = detect(app_engine, actor="sec_kim")  # 담당자 kim_sec의 플랫폼 계정
    set_status(admin_engine, case, status)
    before = history_count(app_engine, case)

    res = act(as_user("kim_sec"), case, action, **body)

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "SELF_REVIEW_FORBIDDEN"
    assert status_of(app_engine, case)[0] == status  # 상태 그대로
    assert history_count(app_engine, case) == before  # 상태 이력에 남기지 않는다


@pytest.mark.parametrize(("status", "action", "body"), OFFICER_TRANSITIONS)
def test_unlinked_officer_is_matched_by_login_id(
    admin_engine, app_engine, as_user, status, action, body
):
    case = detect(app_engine, actor="ops_choi")  # 담당자와 같은 아이디의 플랫폼 계정
    set_status(admin_engine, case, status)

    res = act(as_user("ops_choi"), case, action, **body)

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "SELF_REVIEW_FORBIDDEN"
    assert status_of(app_engine, case)[0] == status


def test_another_officer_can_process_the_case(app_engine, as_user):
    case = detect(app_engine, actor="sec_kim")
    officer = as_user("officer")

    assert act(officer, case, "request", message="소명 바랍니다").status_code == 200
    assert act(officer, case, "dismiss", reason="업무상 정상 확인").json()["status"] == "DISMISSED"


def test_officer_can_still_process_others_cases(app_engine, as_user):
    # 본인 판정은 행위자 기준 — 연결된 담당자도 남의 건은 처리한다
    case = detect(app_engine, actor="ops_park")
    assert act(as_user("kim_sec"), case, "dismiss", reason="정상").status_code == 200


def test_same_login_on_another_source_is_not_own_case(admin_engine, app_engine, as_user):
    # 연결 없는 담당자의 아이디 판정은 출처 PLATFORM 기록에만 — 다른 출처의 같은 아이디는 남이다
    case = detect(app_engine, actor="ops_choi")
    with admin_engine.begin() as conn:
        argus_source = conn.execute(
            select(source_system_table.c.id).where(source_system_table.c.code == "ARGUS")
        ).scalar_one()
        conn.execute(
            update(detection).where(detection.c.id == case).values(source_system_id=argus_source)
        )
    assert act(as_user("ops_choi"), case, "dismiss", reason="정상").status_code == 200


def test_detail_marks_own_case_read_only(app_engine, as_user):
    case = detect(app_engine, actor="sec_kim")

    own = as_user("kim_sec").get(f"/api/detections/{case}")
    other = as_user("officer").get(f"/api/detections/{case}")

    assert own.status_code == 200  # 열람은 된다
    assert own.json()["own_case"] is True
    assert other.json()["own_case"] is False


def test_refused_attempt_is_a_failure_in_argus_self_log(admin_engine, app_engine, as_user):
    case = detect(app_engine, actor="sec_kim")
    act(as_user("kim_sec"), case, "dismiss", reason="내 건이라 닫음")

    with app_engine.connect() as conn:
        rows = (
            conn.execute(
                select(access_log)
                .join(
                    source_system_table,
                    source_system_table.c.id == access_log.c.source_system_id,
                )
                .where(
                    source_system_table.c.code == "ARGUS",
                    access_log.c.actor_login_id == "kim_sec",
                    access_log.c.action == "UPDATE",
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "FAILURE"
    assert row["data_category"] == "ACCESS_LOG"
    assert row["context"] == {"target": {"detection_id": case}}
    assert row["subject_count"] == 0
    # 입력한 사유 문장은 원장에 남기지 않는다
    assert "내 건" not in str(dict(row))


def test_allowed_transitions_stay_out_of_self_log(app_engine, as_user):
    # 성공한 상태 변경은 지금처럼 상태 이력에만 — 자체 접속기록 제외 유지
    case = detect(app_engine, actor="sec_kim")
    assert act(as_user("officer"), case, "dismiss", reason="정상").status_code == 200
    with app_engine.connect() as conn:
        updates = conn.execute(select(access_log.c.id).where(access_log.c.action == "UPDATE")).all()
    assert updates == []
