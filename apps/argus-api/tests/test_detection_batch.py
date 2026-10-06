"""탐지 배치 — 커서, 그룹핑, 진행 중 건에만 추가, 실패·재처리 (db-schema 3-7, policy 2)

순찰은 운영과 같은 앱 계정(argus_app 멤버)으로 돌린다 — 권한이 실제로 충분한지까지 확인.
"""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text, update

from app.detection.batch import BATCH_LOCK_KEY, UNREGISTERED_NOTE, group_bucket, run_batch
from app.ledger.append import append_access_logs
from app.models import (
    detection,
    detection_batch_run,
    detection_log,
    detection_rule,
    detection_status_history,
)

from conftest import make_entry, reset_rules

NOW = datetime.now(UTC)


def clear_detections(admin_engine) -> None:
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM detection_log"))
        conn.execute(text("DELETE FROM detection_status_history"))
        conn.execute(text("DELETE FROM explanation"))
        conn.execute(text("DELETE FROM detection"))
        conn.execute(text("DELETE FROM detection_batch_run"))


@pytest.fixture(autouse=True)
def clean_detection(admin_engine, seed_rules):
    """룰은 대량 다운로드만 켠 시드 상태로 시작하고, 끝나면 탐지 결과를 비운다 (소유자 계정)

    다른 룰을 다루는 테스트는 use_rules로 켠다.
    """
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    yield
    clear_detections(admin_engine)
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))


def use_rules(admin_engine, seed_rules, *names: str) -> None:
    reset_rules(admin_engine, seed_rules, enabled=names)


def add_log(app_engine, **overrides) -> int:
    with app_engine.begin() as conn:
        result = append_access_logs(conn, [make_entry(**overrides)], received_at=datetime.now(UTC))
    return result.inserted_ids[0]


def download(app_engine, count: int = 120, *, start: int = 10001, **overrides) -> int:
    ids = [str(n) for n in range(start, start + count)]
    return add_log(
        app_engine,
        action="DOWNLOAD",
        subject_ids=ids,
        subject_count=count,
        request_path="/admin/members/export",
        **overrides,
    )


def detections(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        rows = conn.execute(select(detection).order_by(detection.c.id)).mappings()
        return [dict(r) for r in rows]


def linked_logs(app_engine, detection_id: int) -> list[int]:
    with app_engine.connect() as conn:
        return list(
            conn.execute(
                select(detection_log.c.access_log_id)
                .where(detection_log.c.detection_id == detection_id)
                .order_by(detection_log.c.access_log_id)
            ).scalars()
        )


def set_status(admin_engine, detection_id: int, status: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(update(detection).where(detection.c.id == detection_id).values(status=status))


# ── 시나리오: 120건 다운로드 → 탐지건 ──────────────────────


def test_bulk_download_creates_detected_case(app_engine):
    log_id = download(app_engine, 120)

    result = run_batch(app_engine)

    assert result.status == "SUCCESS" and result.processed == 1 and result.detected == 1
    [case] = detections(app_engine)
    assert case["status"] == "DETECTED" and case["round"] == 0
    assert case["severity"] == "HIGH" and case["actor_login_id"] == "ops_park"
    assert case["group_bucket"] == group_bucket(NOW)
    assert case["log_count"] == 1
    assert linked_logs(app_engine, case["id"]) == [log_id]
    # 탐지 당시 룰 사본 — 룰이 바뀌어도 판단 근거가 남는다
    snapshot = case["rule_snapshot"]
    assert snapshot["name"] == "대량 다운로드" and snapshot["version"] == 1
    assert snapshot["condition"]["all"][1] == {"field": "subject_count", "op": "gte", "value": 50}

    with app_engine.connect() as conn:
        [history] = conn.execute(select(detection_status_history)).mappings().all()
    assert history["from_status"] is None and history["to_status"] == "DETECTED"
    assert history["actor_user_id"] is None  # 시스템(탐지 배치)
    assert history["comment"] == f"탐지 배치 #{result.run_id}"


@pytest.mark.parametrize(("count", "expected"), [(49, 0), (50, 1)])
def test_threshold_boundary(app_engine, count, expected):
    download(app_engine, count)
    run_batch(app_engine)
    assert len(detections(app_engine)) == expected


def test_large_read_is_not_a_download(app_engine):
    add_log(app_engine, action="READ", subject_ids=["10001"], subject_count=120)
    assert run_batch(app_engine).detected == 0


# ── 그룹핑 ────────────────────────────────────────────────


def test_same_actor_same_day_is_one_case(app_engine):
    first = download(app_engine, 120, occurred_at=NOW - timedelta(minutes=10))
    run_batch(app_engine)
    second = download(app_engine, 80, start=10101, occurred_at=NOW)

    result = run_batch(app_engine)

    assert result.detected == 0  # 새 건이 아니라 기존 건에 추가
    [case] = detections(app_engine)
    assert linked_logs(app_engine, case["id"]) == [first, second]
    assert case["log_count"] == 2
    assert case["last_occurred_at"] == NOW
    summary = case["log_summary"]
    assert summary["subject_count_sum"] == 200
    # 10001~10120과 10101~10180 → 겹치는 20명을 빼면 고유 180명 (피해 범위 판단용)
    assert summary["distinct_subject_count"] == 180


def test_different_actor_or_day_are_separate_cases(app_engine):
    download(app_engine, occurred_at=NOW)
    download(app_engine, occurred_at=NOW, actor_login_id="mkt_lee")
    download(app_engine, occurred_at=NOW - timedelta(days=1))
    assert run_batch(app_engine).detected == 3


def test_date_bucket_uses_korean_time(app_engine):
    # 2026-10-01 15:30 UTC = 2026-10-02 00:30 KST → UTC 날짜로 묶으면 전날로 가 버린다
    download(app_engine, occurred_at=datetime(2026, 10, 1, 15, 30, tzinfo=UTC))
    download(app_engine, occurred_at=datetime(2026, 10, 1, 14, 59, tzinfo=UTC))
    run_batch(app_engine)
    assert sorted(c["group_bucket"] for c in detections(app_engine)) == ["2026-10-01", "2026-10-02"]


# ── 진행 중 건에만 추가 (policy 2-3) ───────────────────────


@pytest.mark.parametrize("closed", ["APPROVED", "DISMISSED", "ESCALATED"])
def test_new_case_after_the_group_was_closed(app_engine, admin_engine, closed):
    download(app_engine)
    run_batch(app_engine)
    [case] = detections(app_engine)
    set_status(admin_engine, case["id"], closed)  # 오전에 소명 승인·종결

    later = download(app_engine)  # 오후에 같은 행위
    assert run_batch(app_engine).detected == 1

    old, new = detections(app_engine)
    assert new["status"] == "DETECTED" and new["group_bucket"] == old["group_bucket"]
    assert linked_logs(app_engine, new["id"]) == [later]  # 종결된 건에 조용히 묻히지 않는다
    assert old["log_count"] == 1


@pytest.mark.parametrize("still_open", ["REQUESTED", "SUBMITTED", "REJECTED"])
def test_open_case_receives_new_logs(app_engine, admin_engine, still_open):
    download(app_engine)
    run_batch(app_engine)
    [case] = detections(app_engine)
    set_status(admin_engine, case["id"], still_open)

    download(app_engine)
    assert run_batch(app_engine).detected == 0
    [case] = detections(app_engine)
    assert case["status"] == still_open and case["log_count"] == 2


# ── 커서·실행 이력 ────────────────────────────────────────


def test_cursor_moves_forward(app_engine):
    download(app_engine)
    first = run_batch(app_engine)
    download(app_engine, actor_login_id="mkt_lee")
    second = run_batch(app_engine)

    assert (first.from_id, first.to_id) == (0, first.to_id)
    assert second.from_id == first.to_id and second.processed == 1


def test_empty_run_is_still_recorded(app_engine):
    # 새 기록이 없어도 "탐지가 주기적으로 수행됐다"는 증적 (§8②)
    result = run_batch(app_engine)
    assert result.status == "SUCCESS" and result.processed == 0
    with app_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(detection_batch_run)).scalar_one() == 1


def test_backlog_is_processed_in_chunks(app_engine):
    ids = [download(app_engine, actor_login_id=f"user{n}") for n in range(3)]
    first = run_batch(app_engine, max_logs=2)
    second = run_batch(app_engine, max_logs=2)
    assert first.processed == 2 and second.processed == 1
    assert second.to_id == ids[-1]
    assert len(detections(app_engine)) == 3


# ── 실패·재처리 ───────────────────────────────────────────


def test_unevaluable_rule_stops_the_run_and_keeps_the_cursor(app_engine, admin_engine):
    download(app_engine)
    with admin_engine.begin() as conn:
        conn.execute(
            update(detection_rule).values(
                condition={"all": [{"field": "actor_mood", "op": "eq", "value": "angry"}]}
            )
        )

    failed = run_batch(app_engine)

    assert failed.status == "FAILED" and "not evaluable" in failed.error
    assert detections(app_engine) == []

    # 룰을 고치면 같은 범위를 처음부터 다시 본다 — 그 사이 기록이 누락되지 않는다
    with admin_engine.begin() as conn:
        conn.execute(
            update(detection_rule).values(
                condition={"all": [{"field": "action", "op": "eq", "value": "DOWNLOAD"}]}
            )
        )
    retried = run_batch(app_engine)
    assert retried.status == "SUCCESS" and retried.from_id == failed.from_id
    assert retried.detected == 1


def test_reprocessing_the_same_range_does_not_duplicate(app_engine, admin_engine):
    download(app_engine)
    run_batch(app_engine)
    with admin_engine.begin() as conn:  # 책갈피를 되돌린 상황
        conn.execute(text("DELETE FROM detection_batch_run"))

    again = run_batch(app_engine)

    assert again.status == "SUCCESS" and again.detected == 0
    [case] = detections(app_engine)
    assert case["log_count"] == 1


def test_reprocessing_after_close_does_not_copy_evidence(app_engine, admin_engine):
    download(app_engine)
    run_batch(app_engine)
    [case] = detections(app_engine)
    set_status(admin_engine, case["id"], "APPROVED")
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM detection_batch_run"))

    assert run_batch(app_engine).detected == 0  # 이미 판단이 끝난 기록으로 새 건을 만들지 않는다
    assert len(detections(app_engine)) == 1


def test_stale_running_run_is_closed(app_engine, admin_engine):
    with admin_engine.begin() as conn:
        conn.execute(
            detection_batch_run.insert().values(
                from_access_log_id=0, to_access_log_id=0, status="RUNNING"
            )
        )
    run_batch(app_engine)
    with app_engine.connect() as conn:
        statuses = conn.execute(
            select(detection_batch_run.c.status, detection_batch_run.c.error).order_by(
                detection_batch_run.c.id
            )
        ).all()
    assert statuses[0] == ("FAILED", "worker restarted")
    assert statuses[1][0] == "SUCCESS"


def test_only_one_batch_runs_at_a_time(app_engine, admin_engine):
    with admin_engine.connect() as other_worker:
        other_worker.execute(select(func.pg_advisory_lock(BATCH_LOCK_KEY)))
        try:
            assert run_batch(app_engine) is None
        finally:
            other_worker.execute(select(func.pg_advisory_unlock(BATCH_LOCK_KEY)))
    assert run_batch(app_engine).status == "SUCCESS"


# ── 룰 필터·요약 ──────────────────────────────────────────


def test_disabled_rule_and_other_access_path_are_ignored(app_engine, admin_engine):
    download(app_engine, access_path="DB")  # APP 룰은 DB 경로 기록에 적용하지 않는다
    assert run_batch(app_engine).detected == 0

    with admin_engine.begin() as conn:
        conn.execute(update(detection_rule).values(enabled=False))
    download(app_engine)
    assert run_batch(app_engine).detected == 0


def test_summary_has_counts_but_no_member_ids(app_engine):
    ids = [str(n) for n in range(10001, 11001)]
    add_log(
        app_engine,
        action="DOWNLOAD",
        subject_ids=ids,
        subject_count=1500,
        subject_truncated=True,  # 1,500명 중 1,000명만 실려 옴
        client_ip="203.0.113.10",
    )
    run_batch(app_engine)
    [case] = detections(app_engine)
    summary = case["log_summary"]

    assert summary["subject_count_sum"] == 1500
    assert summary["distinct_subject_count"] == 1000
    assert summary["subject_ids_truncated"] is True  # 고유 인원은 하한값
    assert summary["actions"] == ["DOWNLOAD"] and summary["ip_list"] == ["203.0.113.10"]
    assert "10001" not in json.dumps(summary)  # 최소처리 — 숫자만, 회원 PK는 없다


# ── 자동 소명 요청 (2026-10-01 사용자 확정) ────────────────


def add_handler_account(admin_engine, login_id="ops_park", status="ACTIVE", employed=True):
    with admin_engine.begin() as conn:
        handler_id = conn.execute(
            text(
                """INSERT INTO handler (source_system_id, login_id, name, team,
                                        employment_status, terminated_at, last_event_at)
                   VALUES (1, :login, '박지훈', 'OPS', :emp, :term, now()) RETURNING id"""
            ),
            {
                "login": login_id,
                "emp": "ACTIVE" if employed else "TERMINATED",
                "term": None if employed else NOW,
            },
        ).scalar_one()
        conn.execute(
            text(
                """INSERT INTO argus_user (login_id, password_hash, role, handler_id, status)
                   VALUES (:login, 'unusable', 'HANDLER', :hid, :status)"""
            ),
            {"login": login_id, "hid": handler_id, "status": status},
        )


@pytest.fixture
def cleanup_accounts(admin_engine):
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))


@pytest.mark.parametrize("status", ["ACTIVE", "LOCKED"])
def test_new_case_is_auto_requested(app_engine, admin_engine, cleanup_accounts, status):
    add_handler_account(admin_engine, status=status)  # 잠김은 풀면 되는 일시 상태 — 요청은 간다
    download(app_engine)

    result = run_batch(app_engine)

    [case] = detections(app_engine)
    assert case["status"] == "REQUESTED" and case["round"] == 1
    with app_engine.connect() as conn:
        [request] = conn.execute(text("SELECT * FROM explanation")).mappings().all()
        history = conn.execute(
            select(
                detection_status_history.c.from_status,
                detection_status_history.c.to_status,
                detection_status_history.c.actor_user_id,
            ).order_by(detection_status_history.c.id)
        ).all()
    assert request["round"] == 1 and request["requested_by"] is None  # 시스템 요청
    assert request["request_message"] == "자동 소명 요청 — 대량 다운로드"
    assert history == [(None, "DETECTED", None), ("DETECTED", "REQUESTED", None)]
    assert case["rule_snapshot"]["auto_request"] is True
    assert result.detected == 1


def test_same_day_repeats_do_not_send_another_request(app_engine, admin_engine, cleanup_accounts):
    # "하루 1건" — 그룹핑(취급자·룰·KST 날짜)으로 요청도 한 번뿐
    add_handler_account(admin_engine)
    download(app_engine)
    run_batch(app_engine)
    download(app_engine)
    run_batch(app_engine)
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM explanation")).scalar_one() == 1
    [case] = detections(app_engine)
    assert case["log_count"] == 2 and case["round"] == 1


@pytest.mark.parametrize(
    ("account", "reason"),
    [
        (None, "no-account"),  # 미동기화
        ({"status": "DISABLED"}, "disabled"),
        ({"employed": False}, "terminated"),
    ],
    ids=["no-account", "disabled", "terminated"],
)
def test_case_stays_detected_when_nobody_can_answer(
    app_engine, admin_engine, cleanup_accounts, account, reason
):
    if account is not None:
        add_handler_account(admin_engine, **account)
    download(app_engine)
    run_batch(app_engine)
    [case] = detections(app_engine)
    assert case["status"] == "DETECTED" and case["round"] == 0  # 담당자가 수동 처리


def test_rule_with_auto_request_off_stays_detected(app_engine, admin_engine, cleanup_accounts):
    add_handler_account(admin_engine)
    with admin_engine.begin() as conn:
        conn.execute(update(detection_rule).values(auto_request=False))
    download(app_engine)
    run_batch(app_engine)
    assert detections(app_engine)[0]["status"] == "DETECTED"


# ── 야간·주말·퇴직자 계정 접속 (기능 레이어 1) ──────────────
# 시각은 모두 고정값 — 테스트를 실행하는 시각과 무관하게 같은 결과가 나와야 한다.
# 2026-09-29(화)·2026-10-03(토)·2026-10-05(월)은 한국 시각 기준 요일이다.

KST = timezone(timedelta(hours=9))


def kst(*args) -> datetime:
    return datetime(*args, tzinfo=KST)


def rule_names(app_engine) -> list[str]:
    return [d["rule_snapshot"]["name"] for d in detections(app_engine)]


def first_history_comment(app_engine, detection_id: int) -> str:
    with app_engine.connect() as conn:
        return (
            conn.execute(
                select(detection_status_history.c.comment)
                .where(detection_status_history.c.detection_id == detection_id)
                .order_by(detection_status_history.c.id)
            )
            .scalars()
            .first()
        )


@pytest.mark.parametrize(
    ("occurred_at", "action", "expected"),
    [
        (kst(2026, 9, 29, 21, 59), "READ", False),
        (kst(2026, 9, 29, 22, 0), "READ", True),  # 시작 포함
        (kst(2026, 9, 30, 3, 0), "DOWNLOAD", True),  # 자정 넘김
        (kst(2026, 9, 30, 5, 59, 59), "READ", True),
        (kst(2026, 9, 30, 6, 0), "READ", False),  # 끝 미포함
        (kst(2026, 9, 29, 23, 0), "LOGIN", False),  # 조회·다운로드만
    ],
    ids=["21:59", "22:00", "03:00-download", "05:59:59", "06:00", "login"],
)
def test_night_access(app_engine, admin_engine, seed_rules, occurred_at, action, expected):
    use_rules(admin_engine, seed_rules, "야간 접속")
    add_log(app_engine, occurred_at=occurred_at, action=action)
    run_batch(app_engine)
    assert rule_names(app_engine) == (["야간 접속"] if expected else [])


def test_night_access_is_judged_in_korean_time(app_engine, admin_engine, seed_rules):
    use_rules(admin_engine, seed_rules, "야간 접속")
    add_log(app_engine, occurred_at=datetime(2026, 9, 29, 14, 0, tzinfo=UTC))  # KST 23:00
    add_log(app_engine, occurred_at=datetime(2026, 9, 29, 23, 0, tzinfo=UTC))  # KST 08:00
    run_batch(app_engine)
    [case] = detections(app_engine)
    assert case["log_count"] == 1 and case["group_bucket"] == "2026-09-29"


def test_weekend_access_uses_korean_weekday(app_engine, admin_engine, seed_rules):
    use_rules(admin_engine, seed_rules, "주말 접속")
    add_log(app_engine, occurred_at=kst(2026, 10, 3, 0, 30), action="LOGIN")  # UTC로는 금요일
    add_log(app_engine, occurred_at=kst(2026, 10, 5, 8, 0))  # UTC로는 일요일
    add_log(app_engine, occurred_at=kst(2026, 10, 1, 14, 0))  # 목요일
    run_batch(app_engine)
    [case] = detections(app_engine)
    assert case["rule_snapshot"]["name"] == "주말 접속" and case["group_bucket"] == "2026-10-03"
    assert case["severity"] == "LOW" and case["log_count"] == 1


def add_terminated_handler(admin_engine, login_id: str, terminated_at: datetime) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO handler (source_system_id, login_id, name, team,
                                        employment_status, terminated_at, last_event_at)
                   VALUES (1, :login, '최유나', 'CS', 'TERMINATED', :term, now())"""
            ),
            {"login": login_id, "term": terminated_at},
        )


def test_terminated_account_is_judged_at_the_time_of_access(
    app_engine, admin_engine, seed_rules, cleanup_accounts
):
    use_rules(admin_engine, seed_rules, "퇴직자 계정 접속")
    terminated_at = kst(2026, 9, 30, 18, 0)
    add_terminated_handler(admin_engine, "cs_choi", terminated_at)
    # 퇴직 전 정상 접속은 지금 퇴직 상태여도 탐지하지 않는다 (소급 탐지 방지)
    add_log(app_engine, actor_login_id="cs_choi", occurred_at=terminated_at - timedelta(hours=1))
    add_log(app_engine, actor_login_id="cs_choi", occurred_at=terminated_at, action="LOGIN")
    add_log(app_engine, actor_login_id="cs_choi", occurred_at=terminated_at + timedelta(days=1))
    add_log(app_engine, occurred_at=terminated_at + timedelta(days=1))  # 재직 중인 ops_park
    add_handler_account(admin_engine)  # ops_park 명부 등록

    run_batch(app_engine)

    cases = detections(app_engine)
    assert [(c["actor_login_id"], c["group_bucket"], c["log_count"]) for c in cases] == [
        ("cs_choi", "2026-09-30", 1),
        ("cs_choi", "2026-10-01", 1),
    ]
    # 퇴직자에게는 소명을 받을 계정이 없다 — 담당자가 처리
    assert all(c["status"] == "DETECTED" and c["severity"] == "HIGH" for c in cases)
    assert UNREGISTERED_NOTE not in first_history_comment(app_engine, cases[0]["id"])


def test_account_missing_from_roster_is_detected_with_reason(app_engine, admin_engine, seed_rules):
    # 명부에 없는 계정 = 퇴직 여부 판정 불가 → 넘기지 않고 탐지 (2026-10-01 사용자 결정)
    use_rules(admin_engine, seed_rules, "퇴직자 계정 접속", "대량 다운로드")
    download(app_engine, actor_login_id="ghost_kim", occurred_at=kst(2026, 9, 29, 10, 0))

    run_batch(app_engine)

    by_rule = {d["rule_snapshot"]["name"]: d for d in detections(app_engine)}
    assert set(by_rule) == {"퇴직자 계정 접속", "대량 다운로드"}
    retired = by_rule["퇴직자 계정 접속"]
    assert retired["status"] == "DETECTED"
    assert first_history_comment(app_engine, retired["id"]).endswith(UNREGISTERED_NOTE)
    # 명부를 보지 않는 룰의 탐지건에는 붙이지 않는다
    bulk = by_rule["대량 다운로드"]
    assert UNREGISTERED_NOTE not in first_history_comment(app_engine, bulk["id"])


def test_argus_self_access_logs_are_not_evaluated(app_engine, admin_engine, seed_rules):
    # Argus 자체 기록(출처 ARGUS = 2)은 원장에 남지만 룰로 평가하지 않는다 (2026-10-01 결정)
    use_rules(
        admin_engine, seed_rules, "대량 다운로드", "야간 접속", "주말 접속", "퇴직자 계정 접속"
    )
    night_saturday = kst(2026, 10, 3, 23, 0)
    add_log(app_engine, source_system_id=2, actor_login_id="officer", occurred_at=night_saturday)
    download(app_engine, source_system_id=2, actor_login_id="officer", occurred_at=night_saturday)

    result = run_batch(app_engine)

    assert result.processed == 2  # 순찰은 했다 — 평가만 하지 않음
    assert result.detected == 0 and detections(app_engine) == []


# ── AGGREGATE: 대량 조회·전월 대비 급증 (기능 레이어 4) ─────


def add_reads(app_engine, times: list[datetime], **overrides) -> list[int]:
    """조회 기록 여러 건을 한 번에 (회원 목록 한 화면 = 20명)"""
    entries = [
        make_entry(
            action="READ",
            occurred_at=t,
            subject_ids=[str(n) for n in range(10001, 10021)],
            subject_count=20,
            request_path="/admin/members",
            **overrides,
        )
        for t in times
    ]
    with app_engine.begin() as conn:
        return append_access_logs(conn, entries, received_at=datetime.now(UTC)).inserted_ids


def spread(start: datetime, count: int, span: timedelta) -> list[datetime]:
    return [start + span * i / count for i in range(count)]


def aggregate_cases(app_engine, name: str) -> list[dict]:
    return [d for d in detections(app_engine) if d["rule_snapshot"]["name"] == name]


@pytest.mark.parametrize(("count", "expected"), [(99, 0), (100, 1)])
def test_bulk_read_threshold_in_a_clock_hour(app_engine, admin_engine, seed_rules, count, expected):
    use_rules(admin_engine, seed_rules, "대량 조회")
    add_reads(app_engine, spread(kst(2026, 9, 15, 14, 0), count, timedelta(minutes=59)))
    run_batch(app_engine)
    cases = aggregate_cases(app_engine, "대량 조회")
    assert len(cases) == expected
    if expected:
        [case] = cases
        assert case["group_bucket"] == "2026-09-15T14:00+09:00"  # 윈도우 시작 시각
        assert case["aggregate_value"] == 100 and case["log_count"] == 100
        assert case["severity"] == "HIGH"
        assert "집계 100 ≥ 기준 100" in first_history_comment(app_engine, case["id"])


def test_bulk_read_does_not_span_two_clock_hours(app_engine, admin_engine, seed_rules):
    use_rules(admin_engine, seed_rules, "대량 조회")
    # 14:30~15:29에 120건이어도 정각 기준으로 60 + 60
    add_reads(app_engine, spread(kst(2026, 9, 15, 14, 30), 120, timedelta(minutes=59)))
    run_batch(app_engine)
    assert aggregate_cases(app_engine, "대량 조회") == []


def test_window_is_recounted_across_batches(app_engine, admin_engine, seed_rules):
    # 순찰 경계에서 윈도우가 쪼개져도 원장에서 통째로 다시 센다
    use_rules(admin_engine, seed_rules, "대량 조회")
    times = spread(kst(2026, 9, 15, 14, 0), 100, timedelta(minutes=59))
    add_reads(app_engine, times[:60])
    run_batch(app_engine)
    assert aggregate_cases(app_engine, "대량 조회") == []
    add_reads(app_engine, times[60:])  # 늦게 도착한 기록 포함
    run_batch(app_engine)
    [case] = aggregate_cases(app_engine, "대량 조회")
    assert case["log_count"] == 100


def test_open_window_case_receives_late_logs_and_new_value(app_engine, admin_engine, seed_rules):
    use_rules(admin_engine, seed_rules, "대량 조회")
    add_reads(app_engine, spread(kst(2026, 9, 15, 14, 0), 100, timedelta(minutes=50)))
    run_batch(app_engine)
    add_reads(app_engine, [kst(2026, 9, 15, 14, 55)] * 5)
    assert run_batch(app_engine).detected == 0  # 새 건이 아니라 기존 건에
    [case] = aggregate_cases(app_engine, "대량 조회")
    assert case["aggregate_value"] == 105 and case["log_count"] == 105


def test_closed_window_is_not_detected_again(app_engine, admin_engine, seed_rules):
    # 집계는 누적이라 종결 뒤에도 같은 윈도우가 계속 기준을 넘는다 — 한 번만 판단 (확인 대기 결정)
    use_rules(admin_engine, seed_rules, "대량 조회")
    add_reads(app_engine, spread(kst(2026, 9, 15, 14, 0), 100, timedelta(minutes=50)))
    run_batch(app_engine)
    [case] = aggregate_cases(app_engine, "대량 조회")
    set_status(admin_engine, case["id"], "APPROVED")
    add_reads(app_engine, [kst(2026, 9, 15, 14, 55)] * 5)
    assert run_batch(app_engine).detected == 0
    assert len(aggregate_cases(app_engine, "대량 조회")) == 1


def weekday_reads(year: int, month: int, per_day: int) -> list[datetime]:
    day = kst(year, month, 1)
    times = []
    while day.month == month:
        if day.weekday() < 5:
            times += spread(day + timedelta(hours=10), per_day, timedelta(hours=6))
        day += timedelta(days=1)
    return times


@pytest.mark.parametrize(("august_per_day", "expected"), [(3, True), (2, False)])
def test_surge_compares_with_the_previous_month(
    app_engine, admin_engine, seed_rules, august_per_day, expected
):
    # 지난 달끼리라 "같은 기간" = 달 전체. 7월 평일 23일 × 1건 = 23건(기준선 ≥ 20)
    # 8월 평일 21일 × 3건 = 63건 → 2.74배 탐지 / × 2건 = 42건 → 1.83배 미탐지
    use_rules(admin_engine, seed_rules, "전월 대비 급증")
    add_reads(app_engine, weekday_reads(2026, 7, 1))
    add_reads(app_engine, weekday_reads(2026, 8, august_per_day))
    run_batch(app_engine)
    cases = aggregate_cases(app_engine, "전월 대비 급증")
    # 7월 윈도우는 6월 기록이 없어(기준선 0) 판정하지 않는다
    assert [c["group_bucket"] for c in cases] == (["2026-08-01T00:00+09:00"] if expected else [])
    if expected:
        [case] = cases
        assert float(case["aggregate_value"]) == pytest.approx(63 / 23, abs=1e-4)
        assert case["log_count"] == 63 and case["severity"] == "MEDIUM"
        comment = first_history_comment(app_engine, case["id"])
        assert "당월 63 / 전월 동기 23" in comment


def test_surge_needs_a_baseline(app_engine, admin_engine, seed_rules):
    # 기준선이 min_baseline(20) 미만이면 비율을 믿을 수 없어 판정하지 않는다
    use_rules(admin_engine, seed_rules, "전월 대비 급증")
    add_reads(app_engine, [kst(2026, 7, 6, 10, 0)] * 19)  # 7월 19건
    add_reads(app_engine, weekday_reads(2026, 8, 5))  # 8월 105건
    run_batch(app_engine)
    assert aggregate_cases(app_engine, "전월 대비 급증") == []


# ── 결제수단 조회 (기능 레이어 7 ①) ──────────────────────────


def test_payment_full_view_is_detected_every_time(app_engine, admin_engine, seed_rules):
    use_rules(admin_engine, seed_rules, "결제수단 조회")
    when = kst(2026, 10, 1, 14, 0)
    path = "/admin/members/{member_id}/refund-account"
    add_log(app_engine, occurred_at=when, data_category="PAYMENT", request_path=path)
    # 평소 회원 상세(끝 4자리만)·주문 목록은 결제수단 조회가 아니다
    add_log(app_engine, occurred_at=when, data_category="MEMBER_BASIC")
    add_log(app_engine, occurred_at=when, data_category="ORDER", request_path="/admin/orders")

    run_batch(app_engine)

    [case] = detections(app_engine)
    assert case["rule_snapshot"]["name"] == "결제수단 조회"
    assert case["severity"] == "HIGH" and case["log_count"] == 1


# ── 경로 구분·DB 직접 접근 기본 룰 (기능 레이어 8 ③, policy 1-5) ─────

DB = {"access_path": "DB"}


def test_all_path_rule_keeps_each_path_in_its_own_case(app_engine, admin_engine):
    # 적용 경로가 "전체"인 룰도 화면 경유·DB 직접 기록을 한 탐지건에 섞지 않는다
    with admin_engine.begin() as conn:
        conn.execute(
            update(detection_rule)
            .where(detection_rule.c.name == "대량 다운로드")
            .values(access_path="ALL")
        )
    app_log = download(app_engine)
    db_log = download(app_engine, **DB)
    assert run_batch(app_engine).detected == 2

    cases = {c["access_path"]: c for c in detections(app_engine)}
    assert set(cases) == {"APP", "DB"}
    assert linked_logs(app_engine, cases["APP"]["id"]) == [app_log]
    assert linked_logs(app_engine, cases["DB"]["id"]) == [db_log]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (DB, True),  # DB 툴로 회원 조회
        ({**DB, "data_category": "PAYMENT"}, True),
        ({**DB, "action": "UPDATE"}, True),  # 앱을 거치지 않은 변경도
        ({**DB, "data_category": "NONE"}, False),  # 업무 외 테이블
        ({}, False),  # 화면 경유 기록은 3티어 룰이 본다
    ],
)
def test_db_night_access(app_engine, admin_engine, seed_rules, overrides, expected):
    use_rules(admin_engine, seed_rules, "DB 직접 야간 접근")
    add_log(app_engine, occurred_at=kst(2026, 10, 6, 23, 0), **overrides)
    run_batch(app_engine)
    found = [
        (c["rule_snapshot"]["name"], c["access_path"], c["severity"])
        for c in detections(app_engine)
    ]
    assert found == ([("DB 직접 야간 접근", "DB", "HIGH")] if expected else [])


def test_db_weekend_access(app_engine, admin_engine, seed_rules):
    use_rules(admin_engine, seed_rules, "DB 직접 주말 접근")
    add_log(app_engine, occurred_at=kst(2026, 10, 10, 14, 0), **DB)  # 토요일
    add_log(app_engine, occurred_at=kst(2026, 10, 12, 14, 0), **DB)  # 월요일
    run_batch(app_engine)
    [case] = detections(app_engine)
    assert case["access_path"] == "DB" and case["severity"] == "MEDIUM"
    assert case["group_bucket"] == "2026-10-10"


def test_db_surge_counts_db_path_only(app_engine, admin_engine, seed_rules):
    # 7월 DB 23건 → 8월 DB 63건 = 2.74배. 화면 경유 기록이 많아도 DB 집계·기준선에 섞이지 않는다
    use_rules(admin_engine, seed_rules, "DB 직접 전월 대비 급증")
    add_reads(app_engine, weekday_reads(2026, 7, 1), **DB)
    add_reads(app_engine, weekday_reads(2026, 7, 5))
    add_reads(app_engine, weekday_reads(2026, 8, 3), **DB)
    run_batch(app_engine)
    [case] = aggregate_cases(app_engine, "DB 직접 전월 대비 급증")
    assert case["access_path"] == "DB" and case["log_count"] == 63
    assert float(case["aggregate_value"]) == pytest.approx(63 / 23, abs=1e-4)


def test_all_path_aggregate_has_a_baseline_per_path(app_engine, admin_engine, seed_rules):
    # "전체" 경로 집계 룰은 경로별로 따로 센다 — 기준선도 같은 경로 안에서만 (policy 1-5)
    use_rules(admin_engine, seed_rules, "전월 대비 급증")
    with admin_engine.begin() as conn:
        conn.execute(
            update(detection_rule)
            .where(detection_rule.c.name == "전월 대비 급증")
            .values(access_path="ALL")
        )
    add_reads(app_engine, weekday_reads(2026, 7, 1))  # 화면 경유 7월 23건
    add_reads(app_engine, weekday_reads(2026, 8, 3))  # 화면 경유 8월 63건 → 탐지
    add_reads(
        app_engine, weekday_reads(2026, 8, 3), **DB
    )  # DB 8월 63건 — DB 기준선 없음 → 판정 안 함
    run_batch(app_engine)
    cases = aggregate_cases(app_engine, "전월 대비 급증")
    assert [(c["access_path"], c["log_count"]) for c in cases] == [("APP", 63)]
