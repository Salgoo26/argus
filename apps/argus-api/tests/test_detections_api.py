"""탐지건 조회·소명 흐름 API — 마스킹, 노출 범위, 상태 전이, 자체 접속기록 (M4)"""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.detection.batch import run_batch
from app.ledger.append import append_access_logs
from app.models import access_log, argus_user, detection, handler, source_system

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
    """담당자 officer, 취급자 ops_park·mkt_lee (가상 인물)

    룰은 대량 다운로드만 켠다 — 야간·주말 룰은 테스트 실행 시각에 따라 탐지건을 더 만든다
    """
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    with admin_engine.begin() as conn:
        conn.execute(
            insert(argus_user).values(
                login_id="officer", password_hash=password_hash, role="OFFICER"
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
            "detection_log",
            "detection_status_history",
            "explanation",
            "detection",
            "detection_batch_run",
            "argus_user",
            "handler",
        ):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름


def detect(app_engine, actor="ops_park", count=120, context=None) -> int:
    """actor가 count건을 다운로드 → 순찰 → (계정이 있으니) 자동 소명 요청된 탐지건 id"""
    with app_engine.begin() as conn:
        append_access_logs(
            conn,
            [
                make_entry(
                    actor_login_id=actor,
                    action="DOWNLOAD",
                    subject_ids=[str(n) for n in range(10001, 10001 + count)],
                    subject_count=count,
                    request_path="/admin/members/export",
                    context=context,
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


def act(client, detection_id, action, **body):
    return client.post(f"/api/detections/{detection_id}/{action}", json=body)


def status_of(app_engine, detection_id) -> tuple[str, int]:
    with app_engine.connect() as conn:
        return conn.execute(
            select(detection.c.status, detection.c.round).where(detection.c.id == detection_id)
        ).one()


# ── 시나리오 (CLAUDE.md 6절, 자동 소명 요청 반영) ──────────


def test_full_explanation_flow(app_engine, as_user):
    case = detect(app_engine)  # 탐지 → 자동 요청 (REQUESTED, 1차)
    officer, ops_park = as_user("officer"), as_user("ops_park")

    assert act(ops_park, case, "submit", content="월말 정산 자료 요청으로 추출").status_code == 200
    assert act(officer, case, "reject", comment="요청 부서·결재 근거 첨부 필요").json() == {
        "id": case,
        "status": "REJECTED",
        "round": 1,
    }
    assert act(officer, case, "request", message="결재 근거와 함께 재제출").json()["round"] == 2
    act(ops_park, case, "submit", content="재무팀 요청 메일(9/30) 근거")
    assert act(officer, case, "approve").json()["status"] == "APPROVED"

    detail = officer.get(f"/api/detections/{case}").json()
    assert detail["status"] == "APPROVED" and detail["closed_at"] is not None
    first, second = detail["explanations"]
    assert first["requested_by"] is None  # 시스템 자동 요청
    assert first["submitted_by"] == "ops_park" and first["review_result"] == "REJECTED"
    assert second["requested_by"] == "officer" and second["review_result"] == "APPROVED"
    assert [(h["to_status"], h["actor"]) for h in detail["history"]] == [
        ("DETECTED", None),
        ("REQUESTED", None),
        ("SUBMITTED", "ops_park"),
        ("REJECTED", "officer"),
        ("REQUESTED", "officer"),
        ("SUBMITTED", "ops_park"),
        ("APPROVED", "officer"),
    ]


def test_officer_cancels_a_false_positive_request(app_engine, as_user):
    case = detect(app_engine)
    officer = as_user("officer")

    res = act(officer, case, "dismiss", reason="정기 정산 다운로드 — 오탐")

    assert res.json()["status"] == "DISMISSED"
    detail = officer.get(f"/api/detections/{case}").json()
    assert detail["close_reason"] == "정기 정산 다운로드 — 오탐" and detail["closed_at"]
    assert detail["history"][-1]["actor"] == "officer"


def test_manual_request_and_dismiss_for_unassigned_case(app_engine, admin_engine, as_user):
    # 계정이 없는 행위자 → DETECTED로 남음 → 담당자 수동 처리
    case = detect(app_engine, actor="cs_kim")
    assert status_of(app_engine, case) == ("DETECTED", 0)
    officer = as_user("officer")
    assert (
        act(officer, case, "dismiss", reason="퇴직자 계정 정리 중 발생 — 확인 완료").status_code
        == 200
    )


def test_escalate_after_rejection(app_engine, as_user):
    case = detect(app_engine)
    officer, ops_park = as_user("officer"), as_user("ops_park")
    act(ops_park, case, "submit", content="업무상 필요")
    act(officer, case, "reject", comment="근거 부족")
    assert act(officer, case, "escalate", comment="정보보안팀 이관").json()["status"] == "ESCALATED"


# ── 허용되지 않는 전이·역할 ──────────────────────────────


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("approve", {}),
        ("reject", {"comment": "x"}),
        ("escalate", {}),
        ("request", {}),  # 이미 요청됨
    ],
)
def test_invalid_transition_is_409(app_engine, as_user, action, body):
    case = detect(app_engine)  # REQUESTED
    res = act(as_user("officer"), case, action, **body)
    assert res.status_code == 409 and res.json()["error"]["code"] == "INVALID_TRANSITION"
    assert status_of(app_engine, case) == ("REQUESTED", 1)


def test_closed_case_cannot_move(app_engine, as_user):
    case = detect(app_engine)
    officer = as_user("officer")
    act(officer, case, "dismiss", reason="오탐")
    for action, body in (("request", {}), ("dismiss", {"reason": "x"}), ("approve", {})):
        assert act(officer, case, action, **body).status_code == 409


@pytest.mark.parametrize(
    ("who", "action", "body"),
    [
        ("officer", "submit", {"content": "대신 제출"}),  # 담당자는 소명을 대신 쓸 수 없다
        ("ops_park", "dismiss", {"reason": "제가 보기엔 오탐"}),  # 취급자의 취소 경로는 없다
        ("ops_park", "approve", {}),
    ],
)
def test_wrong_role_is_403(app_engine, as_user, who, action, body):
    case = detect(app_engine)
    res = act(as_user(who), case, action, **body)
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("dismiss", {}),
        ("dismiss", {"reason": "   "}),
        ("reject", {}),
        ("submit", {"content": ""}),
        ("submit", {"content": "x" * 5001}),
    ],
)
def test_required_text_is_400(app_engine, as_user, action, body):
    case = detect(app_engine)
    who = "ops_park" if action == "submit" else "officer"
    assert act(as_user(who), case, action, **body).status_code == 400


# ── 노출 범위 ────────────────────────────────────────────


def test_officer_sees_all_handler_sees_own_requested_only(app_engine, as_user):
    own = detect(app_engine, actor="ops_park")
    other = detect(app_engine, actor="mkt_lee")
    unassigned = detect(app_engine, actor="cs_kim")  # 계정 없음 → DETECTED

    officer_ids = {i["id"] for i in as_user("officer").get("/api/detections").json()["items"]}
    handler_ids = {i["id"] for i in as_user("ops_park").get("/api/detections").json()["items"]}

    assert officer_ids == {own, other, unassigned}
    assert handler_ids == {own}


def test_handler_gets_404_for_others_and_unrequested(app_engine, as_user):
    other = detect(app_engine, actor="mkt_lee")
    ops_park = as_user("ops_park")
    for res in (
        ops_park.get(f"/api/detections/{other}"),
        act(ops_park, other, "submit", content="x"),
        ops_park.get("/api/detections/999999"),
    ):
        assert res.status_code == 404  # 존재 여부를 드러내지 않는다


@pytest.fixture
def auto_request_off(admin_engine):
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE detection_rule SET auto_request = false"))
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE detection_rule SET auto_request = true"))


def test_handler_does_not_see_detected_case_until_requested(app_engine, as_user, auto_request_off):
    case = detect(app_engine)  # 룰의 자동 요청이 꺼져 있어 DETECTED로 남음
    assert status_of(app_engine, case) == ("DETECTED", 0)
    ops_park = as_user("ops_park")
    assert ops_park.get(f"/api/detections/{case}").status_code == 404
    assert act(as_user("officer"), case, "request").json()["round"] == 1  # 수동 요청
    assert ops_park.get(f"/api/detections/{case}").status_code == 200


def test_list_filter_and_paging(app_engine, as_user):
    detect(app_engine, actor="ops_park")
    detect(app_engine, actor="mkt_lee")
    officer = as_user("officer")
    body = officer.get("/api/detections", params={"status": "REQUESTED", "size": 1}).json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert officer.get("/api/detections", params={"status": "WHATEVER"}).status_code == 400


# ── 마스킹 ────────────────────────────────────────────────


@pytest.mark.parametrize("who", ["officer", "ops_park"])
def test_member_ids_are_masked_for_everyone(app_engine, as_user, who):
    case = detect(app_engine, count=120)
    res = as_user(who).get(f"/api/detections/{case}")
    body = res.json()

    [log] = body["logs"]
    assert log["subjects"][0] == "member_10***" and len(log["subjects"]) == 120
    assert log["subject_count"] == 120
    # 원본 식별값("10001" 등)이 응답 어디에도 없다 — 서버에서 가려서 보낸다 (LOG-10)
    assert "10001" not in res.text and "10120" not in res.text
    assert body["actor_name"] == "박지훈" and body["rule"]["name"] == "대량 다운로드"


def test_list_contains_counts_not_identifiers(app_engine, as_user):
    detect(app_engine)
    [item] = as_user("officer").get("/api/detections").json()["items"]
    assert item["subject_count_sum"] == 120 and item["distinct_subject_count"] == 120
    assert "subjects" not in item


# ── 자체 접속기록 ─────────────────────────────────────────


def _argus_logs(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(access_log)
                .join(source_system, source_system.c.id == access_log.c.source_system_id)
                .where(source_system.c.code == "ARGUS", access_log.c.action == "READ")
                .order_by(access_log.c.id)
            ).mappings()
        ]


def test_reads_are_logged_with_counts_only_transitions_are_not(app_engine, as_user):
    case = detect(app_engine, count=120)
    officer = as_user("officer")
    officer.get("/api/detections")
    officer.get(f"/api/detections/{case}")
    act(officer, case, "dismiss", reason="오탐")

    listing, detail = _argus_logs(app_engine)  # 전이(dismiss)는 READ로 남지 않는다
    assert listing["request_path"] == "/api/detections" and listing["subject_count"] == 0
    assert detail["request_path"] == "/api/detections/{detection_id}"
    assert detail["subject_count"] == 120  # 화면에 보여준(마스킹된) 식별값 수
    assert detail["subject_ids"] is None  # 회원 PK를 Argus 원장에 다시 쌓지 않는다
    assert json.dumps(detail, default=str).count("10001") == 0


def test_unauthenticated_is_401(client):
    assert client.get("/api/detections").status_code == 401


def test_detail_shows_business_ticket_but_not_other_context(app_engine, as_user):
    # 1:1 문의 처리 중의 조회 — 플랫폼이 context.ticket_id를 실어 보낸다 (기능 레이어 7 ②)
    case = detect(app_engine, context={"ticket_id": "INQ-12"})
    [log] = as_user("officer").get(f"/api/detections/{case}").json()["logs"]
    assert log["ticket_id"] == "INQ-12" and "context" not in log

    other = detect(app_engine, actor="cs_kim")  # 티켓 없는 기록
    [log] = as_user("officer").get(f"/api/detections/{other}").json()["logs"]
    assert log["ticket_id"] is None


# ── 관련 업무 티켓 (소명과 별도 칸, 2026-10-02) ─────────────────


def test_submit_with_related_tickets_and_officer_sees_platform_links(app_engine, as_user):
    case = detect(app_engine)
    handler_client = as_user("ops_park")
    res = act(
        handler_client,
        case,
        "submit",
        content="CS 요청으로 확인",
        ticket_ids=["INQ-12", " inq-7 ", "INQ-12"],  # 공백·소문자 정리, 중복은 한 번만
    )
    assert res.status_code == 200

    [round1] = as_user("officer").get(f"/api/detections/{case}").json()["explanations"]
    assert round1["tickets"] == [
        {"ticket_id": "INQ-12", "url": "http://localhost:3000/admin/inquiries/12"},
        {"ticket_id": "INQ-7", "url": "http://localhost:3000/admin/inquiries/7"},
    ]


@pytest.mark.parametrize(
    "tickets",
    [
        ["12"],
        ["INQ-0"],
        ["INQ-12/../../members"],
        ["javascript:alert(1)"],
        ["INQ-1", "INQ-2", "INQ-3", "INQ-4"],
    ],
    ids=["no-prefix", "zero", "path", "scheme", "too-many"],
)
def test_invalid_tickets_are_rejected(app_engine, as_user, tickets):
    case = detect(app_engine)
    res = act(as_user("ops_park"), case, "submit", content="소명", ticket_ids=tickets)
    assert res.status_code == 400
    assert status_of(app_engine, case)[0] == "REQUESTED"  # 제출되지 않음


def test_tickets_are_optional(app_engine, as_user):
    case = detect(app_engine)
    assert act(as_user("ops_park"), case, "submit", content="소명").status_code == 200
    [round1] = as_user("officer").get(f"/api/detections/{case}").json()["explanations"]
    assert round1["tickets"] == []
