"""점검 보고서 — 경로별 섹션, 마스킹, EXPORT 기록, 담당자 전용 (기능 레이어 9, LOG-09·policy 6-1)

원장은 append-only라 다른 테스트의 기록이 남는다 — 이 파일은 2025년 3월(다른 테스트가 쓰지 않는
과거 기간)에 기록·탐지건을 만들어 그 기간으로 보고서를 만든다.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.ledger.append import append_access_logs
from app.models import (
    access_log,
    argus_user,
    detection,
    detection_log,
    detection_status_history,
    explanation,
    explanation_attachment,
    handler,
    inspection_report,
    source_system,
)

from conftest import TEST_CLIENT_ADDR, make_entry

PASSWORD = "report-test-password-1"  # 테스트 전용 더미 값
KST = timezone(timedelta(hours=9))
WHEN = datetime(2025, 3, 10, 14, 0, tzinfo=KST)
MARCH = {"date_from": "2025-03-01", "date_to": "2025-03-31"}
REPORTS = "/api/reports"
MEMBER_IDS = [str(n) for n in range(10001, 10121)]  # 120명 (가상 회원번호)


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash):
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
            "detection_log",
            "detection_status_history",
            "explanation_attachment",
            "explanation",
            "detection",
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
        assert (
            c.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD}).status_code
            == 200
        )
        return c

    yield login
    for c in clients:
        c.close()


def _log(app_engine, **overrides) -> int:
    with app_engine.begin() as conn:
        entry = make_entry(occurred_at=WHEN, **overrides)
        return append_access_logs(conn, [entry], received_at=datetime.now(UTC)).inserted_ids[0]


def _case(admin_engine, log_id: int, **values) -> int:
    row = {
        "rule_id": 1,
        "rule_version": 1,
        "rule_snapshot": {"name": values.pop("rule_name"), "version": 1},
        "source_system_id": 1,
        "actor_login_id": "ops_park",
        "group_bucket": "2025-03-10",
        "first_occurred_at": WHEN,
        "last_occurred_at": WHEN,
        "detected_at": WHEN + timedelta(minutes=5),
        "log_count": 1,
    }
    row.update(values)
    with admin_engine.begin() as conn:
        case_id = conn.execute(insert(detection).values(**row).returning(detection.c.id)).scalar()
        conn.execute(insert(detection_log).values(detection_id=case_id, access_log_id=log_id))
    return case_id


@pytest.fixture
def march(app_engine, admin_engine):
    """2025년 3월: 화면 경유 다운로드(120명)·실패 1건, DB 직접 조회 2건(미특정) + 탐지건 2개"""
    app_log = _log(
        app_engine, action="DOWNLOAD", subject_ids=MEMBER_IDS, subject_count=len(MEMBER_IDS)
    )
    _log(app_engine, result="FAILURE", subject_ids=[], subject_count=0)
    db_context = {
        "db_user": "platform_owner",
        "sql_normalized": "SELECT * FROM member",
        "tables": ["member"],
        "row_count": 40,
        "raw_ref": "6f1d0c2e-0000-4000-8000-0000000000aa",
        "raw_fingerprint": "sha256:" + "ab" * 32,
        "subject_unresolved": True,
    }
    db_log = _log(
        app_engine, access_path="DB", subject_ids=[], subject_count=40, context=db_context
    )
    _log(app_engine, access_path="DB", subject_ids=[], subject_count=40, context=db_context)
    app_case = _case(
        admin_engine,
        app_log,
        rule_name="대량 다운로드",
        access_path="APP",
        severity="HIGH",
        status="ESCALATED",
        round=1,
        log_summary={"subject_count_sum": 120, "distinct_subject_count": 120},
    )
    _case(
        admin_engine,
        db_log,
        rule_name="DB 직접 야간 접근",
        access_path="DB",
        severity="HIGH",
        status="REQUESTED",
        round=1,
        log_summary={"subject_count_sum": 40, "distinct_subject_count": 0},
    )
    with admin_engine.begin() as conn:
        officer_id = conn.execute(
            select(argus_user.c.id).where(argus_user.c.login_id == "officer")
        ).scalar()
        conn.execute(
            insert(explanation).values(
                detection_id=app_case,
                round=1,
                requested_by=officer_id,
                submitted_at=WHEN + timedelta(days=1),
                content="가상 소명",
                reviewed_at=WHEN + timedelta(days=2),
                review_result="REJECTED",
            )
        )
    return app_case


def test_report_has_a_section_per_path_with_masked_subjects(as_user, march, app_engine):
    res = as_user("officer").post(REPORTS, json={**MARCH, "scope": "ALL"})
    assert res.status_code == 201, res.text
    body = res.json()
    summary = body["summary"]
    assert body["scope"] == "ALL" and body["unmasked"] is False and body["escalated_count"] == 1
    assert summary["integrity"]["ok"] is True  # §8③ 해시체인 점검 결과

    app, db = summary["paths"]["APP"], summary["paths"]["DB"]  # 합산하지 않고 경로별
    assert app["logs"]["total"] == 2 and app["logs"]["failures"] == 1
    assert app["logs"]["subject_unresolved"] == 0
    assert db["logs"]["total"] == 2 and db["logs"]["subject_unresolved"] == 2
    assert (
        app["detections"]["by_status"] == {"ESCALATED": 1} and app["detections"]["escalated"] == 1
    )
    assert db["detections"]["by_rule"] == [{"name": "DB 직접 야간 접근", "count": 1}]
    assert app["explanations"] == {"requested": 1, "submitted": 1, "approved": 0, "rejected": 1}

    [case] = app["cases"]
    # 마스킹 값 앞 몇 개만 — 화면에서 "외 N명" (policy 6-1)
    assert case["subjects"] == ["member_10***"] * 3 and case["distinct_subject_count"] == 120
    assert case["actor_name"] == "박지훈"
    [db_case] = db["cases"]
    assert db_case["subjects"] == [] and db_case["subject_count_sum"] == 40  # 미특정 40건
    assert not any(f'"{i}"' in res.text or f"member_{i}" in res.text for i in MEMBER_IDS)


def test_report_creation_is_logged_as_export(as_user, march, app_engine):
    report_id = as_user("officer").post(REPORTS, json=MARCH).json()["id"]
    with app_engine.connect() as conn:
        [log] = (
            conn.execute(
                select(access_log)
                .join(source_system, source_system.c.id == access_log.c.source_system_id)
                .where(source_system.c.code == "ARGUS", access_log.c.action == "EXPORT")
                .order_by(access_log.c.id.desc())
                .limit(1)
            )
            .mappings()
            .all()
        )
        stored = (
            conn.execute(select(inspection_report).where(inspection_report.c.id == report_id))
            .mappings()
            .one()
        )
    assert log["actor_login_id"] == "officer" and log["context"] == {"report_id": report_id}
    assert log["subject_count"] == 3  # 보고서에 실린 마스킹 식별값 수 (원본은 남기지 않음)
    assert stored["unmasked"] is False and stored["scope"] == {"access_path": "ALL"}


def test_single_path_scope_has_only_that_section(as_user, march):
    summary = as_user("officer").post(REPORTS, json={**MARCH, "scope": "DB"}).json()["summary"]
    assert list(summary["paths"]) == ["DB"]


def test_snapshot_is_kept_when_cases_change_later(as_user, march, admin_engine):
    officer = as_user("officer")
    report_id = officer.post(REPORTS, json=MARCH).json()["id"]
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE detection SET status = 'APPROVED'"))
    again = officer.get(f"{REPORTS}/{report_id}").json()
    # 보고서는 만든 순간의 숫자 — 점검 증적
    assert again["summary"]["paths"]["APP"]["detections"]["by_status"] == {"ESCALATED": 1}
    assert [r["id"] for r in officer.get(REPORTS).json()["items"]] == [report_id]


def test_reports_are_for_officers_only(as_user):
    handler_client = as_user("ops_park")
    assert handler_client.post(REPORTS, json=MARCH).status_code == 403
    assert handler_client.get(REPORTS).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"date_from": "2025-03-31", "date_to": "2025-03-01"},
        {"date_from": "2024-01-01", "date_to": "2025-03-01"},  # 1년 초과
        {**MARCH, "scope": "WEB"},
    ],
)
def test_invalid_conditions_are_400(as_user, body):
    assert as_user("officer").post(REPORTS, json=body).status_code == 400


def test_unknown_report_is_404(as_user):
    assert as_user("officer").get(f"{REPORTS}/999999").status_code == 404


# ── 탐지건별 처리 내용 (v0.1 보강 B — 실무 결재문서처럼 "어떻게 처리했는지"까지) ──


def _officer_id(conn) -> int:
    return conn.execute(select(argus_user.c.id).where(argus_user.c.login_id == "officer")).scalar()


def test_cases_carry_the_latest_explanation_and_who_handled_it(as_user, march, admin_engine):
    long_content = "주문번호 1234 배송지 확인 업무" + "가" * 250  # 200자 넘는 소명
    with admin_engine.begin() as conn:
        officer_id = _officer_id(conn)
        handler_id = conn.execute(
            select(argus_user.c.id).where(argus_user.c.login_id == "ops_park")
        ).scalar()
        conn.execute(
            insert(explanation).values(
                detection_id=march,
                round=2,
                requested_by=officer_id,
                requested_at=WHEN + timedelta(days=3),
                submitted_by=handler_id,
                submitted_at=WHEN + timedelta(days=4),
                content=long_content,
                ticket_ids=["INQ-7", "INQ-9"],
                reviewed_by=officer_id,
                reviewed_at=WHEN + timedelta(days=5),
                review_result="REJECTED",
                review_comment="근거 티켓과 처리 건수가 맞지 않음",
            )
        )
        second = conn.execute(
            select(explanation.c.id).where(
                explanation.c.detection_id == march, explanation.c.round == 2
            )
        ).scalar()
        for n in range(2):
            conn.execute(
                insert(explanation_attachment).values(
                    explanation_id=second,
                    original_name=f"evidence{n}.png",
                    stored_path=f"/data/attachments/x{n}",
                    content_type="image/png",
                    size_bytes=10,
                    sha256="0" * 64,
                )
            )
        conn.execute(
            insert(detection_status_history).values(
                detection_id=march,
                from_status="REJECTED",
                to_status="ESCALATED",
                round=2,
                actor_user_id=officer_id,
                comment="정보보안팀 이관",
                created_at=WHEN + timedelta(days=6),
            )
        )

    summary = as_user("officer").post(REPORTS, json=MARCH).json()["summary"]
    [case] = summary["paths"]["APP"]["cases"]
    shown = case["explanation"]
    assert shown["round"] == 2  # 마지막 차수
    assert shown["content"] == long_content[:200] + "…"  # 요지 200자
    assert shown["review_result"] == "REJECTED"
    assert shown["review_comment"] == "근거 티켓과 처리 건수가 맞지 않음"
    assert shown["ticket_ids"] == ["INQ-7", "INQ-9"]
    assert shown["attachment_count"] == 2
    assert case["handled_by"] == "officer"
    assert datetime.fromisoformat(case["handled_at"]) == WHEN + timedelta(days=6)
    assert case["close_reason"] is None


def test_dismissed_case_carries_the_cancel_reason(as_user, march, app_engine, admin_engine):
    log_id = _log(app_engine, action="READ", subject_ids=["10001"], subject_count=1)
    case_id = _case(
        admin_engine,
        log_id,
        rule_name="야간 접속",
        access_path="APP",
        severity="LOW",
        status="DISMISSED",
        round=1,
        close_reason="야간 배송 장애 대응 — 사전 승인됨",
        log_summary={"subject_count_sum": 1, "distinct_subject_count": 1},
    )
    with admin_engine.begin() as conn:
        conn.execute(
            insert(detection_status_history).values(
                detection_id=case_id,
                from_status="REQUESTED",
                to_status="DISMISSED",
                round=1,
                actor_user_id=_officer_id(conn),
                comment="야간 배송 장애 대응 — 사전 승인됨",
            )
        )

    summary = as_user("officer").post(REPORTS, json=MARCH).json()["summary"]
    dismissed = next(c for c in summary["paths"]["APP"]["cases"] if c["id"] == case_id)
    assert dismissed["close_reason"] == "야간 배송 장애 대응 — 사전 승인됨"
    assert dismissed["handled_by"] == "officer"
    assert dismissed["explanation"] is None  # 소명 요청 기록이 없는 건


def test_unsubmitted_draft_attachments_are_not_counted(as_user, march, admin_engine):
    # 취급자가 고치는 중인 차수(미제출)의 첨부는 담당자에게도 보이지 않는다 — 보고서도 같다
    with admin_engine.begin() as conn:
        conn.execute(
            insert(explanation).values(
                detection_id=march, round=2, requested_by=_officer_id(conn), ticket_ids=[]
            )
        )
        draft = conn.execute(
            select(explanation.c.id).where(
                explanation.c.detection_id == march, explanation.c.round == 2
            )
        ).scalar()
        conn.execute(
            insert(explanation_attachment).values(
                explanation_id=draft,
                original_name="draft.png",
                stored_path="/data/attachments/d",
                content_type="image/png",
                size_bytes=10,
                sha256="0" * 64,
            )
        )
    summary = as_user("officer").post(REPORTS, json=MARCH).json()["summary"]
    [case] = summary["paths"]["APP"]["cases"]
    assert case["explanation"]["round"] == 2 and case["explanation"]["content"] is None
    assert case["explanation"]["attachment_count"] == 0
