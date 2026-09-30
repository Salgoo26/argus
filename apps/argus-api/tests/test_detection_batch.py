"""탐지 배치 — 커서, 그룹핑, 진행 중 건에만 추가, 실패·재처리 (db-schema 3-7, policy 2)

순찰은 운영과 같은 앱 계정(argus_app 멤버)으로 돌린다 — 권한이 실제로 충분한지까지 확인.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update

from app.detection.batch import BATCH_LOCK_KEY, group_bucket, run_batch
from app.ledger.append import append_access_logs
from app.models import (
    detection,
    detection_batch_run,
    detection_log,
    detection_rule,
    detection_status_history,
)

from conftest import make_entry

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def clean_detection(admin_engine):
    """탐지 결과를 비우고, 테스트가 바꾼 룰을 시드 상태로 되돌린다 (소유자 계정)"""
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM detection_log"))
        conn.execute(text("DELETE FROM detection_status_history"))
        conn.execute(text("DELETE FROM detection"))
        conn.execute(text("DELETE FROM detection_batch_run"))
        conn.execute(text("DELETE FROM detection_rule WHERE name <> '대량 다운로드'"))
        conn.execute(
            text(
                """UPDATE detection_rule SET enabled = true, condition = '{"all": [
                    {"field": "action", "op": "eq", "value": "DOWNLOAD"},
                    {"field": "subject_count", "op": "gte", "value": 50}]}'
                   WHERE name = '대량 다운로드'"""
            )
        )


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
