"""탐지 배치 한 번 — 원장 순찰 (db-schema 3-7, actor-flows F-04, §17① 자동화 분석·탐지)

1. 책갈피: 직전 SUCCESS 실행의 to_access_log_id 초과 ~ 현재 원장 최대 id 이하
   (한 번에 최대 MAX_LOGS건)
   - append가 advisory lock으로 직렬화돼 있어(M1) "id N이 보이면 1..N-1은 모두 커밋됨"이 보장된다
   → id 커서로 누락 없이 읽는다
   (received_at은 시계 오차·동시 삽입 때문에 커서로 쓰지 않음, api-spec 2-6)
2. 실행 이력 RUNNING을 먼저 별도 트랜잭션으로 남긴다 (진행 중인 순찰이 밖에서 보이게)
3. 판정·탐지건 생성·하위 로그 추가·SUCCESS 기록을 **한 트랜잭션**으로 — 중간에 실패하면 전부 취소,
   실행은 FAILED로 남고 책갈피가 그대로라 다음 순찰이 같은 범위를 처음부터 다시 본다
4. 켜진 룰 중 해석할 수 없는 룰이 있으면 그 룰만 건너뛰지 않고 순찰 전체를 FAILED로 멈춘다 —
   건너뛰면 책갈피가 넘어가 그 사이 기록은 그 룰로 영영 평가되지 않는다 (2026-10-01 결정)

탐지건 그룹핑 (policy 2-2·2-3):
- 키 = (룰, 출처, 취급자, 발생 날짜) — 날짜는 **한국 시각(KST)** 기준 (2026-10-01 결정)
- 같은 그룹에 **진행 중**(DETECTED·REQUESTED·SUBMITTED·REJECTED) 건이 있으면 하위 로그만 추가,
  없거나 종결(APPROVED·DISMISSED·ESCALATED)됐으면 새 탐지건
  — 승인은 "그때까지의 행위"에 대한 판단이므로
- 진행 중 건은 그룹당 1개 — DB의 부분 유니크 인덱스(ux_detection_open_group)가 함께 보장한다

평가 대상 (2026-10-01 사용자 결정, EVENT 룰 3개 추가 때):
- 룰은 **감시 대상 시스템(플랫폼)의 기록에만** 적용한다. Argus 자체 접속기록(출처 ARGUS)은 원장에
  남기되(LOG-17) 룰로 평가하지 않는다 — 담당자가 밤에 검토하면 담당자 본인 건이 생기고(자기 점검),
  취급자의 소명 제출이 다시 탐지되는 문제. 자체 기록은 접속기록 조회 화면·점검 보고서에서 본다
- 취급자 명부가 필요한 조건(퇴직 여부)은 순찰마다 명부를 한 번 읽어 판정 — 명부에 없는 계정은
  판정 불가로 탐지하고 상태 이력에 사유를 남긴다 (rules.evaluation_facts)

자동 소명 요청 (2026-10-01 사용자 확정):
새 탐지건은 룰의 auto_request가 켜져 있고 행위자에게 비활성이 아닌 A5 계정이 있으면
같은 트랜잭션에서 바로 REQUESTED(1차, 요청자 = 시스템). 아니면 DETECTED로 남아 담당자가 처리.
자동 요청은 탐지건을 "새로 만들 때"만 — 이미 진행 중인 건에 기록이 보태질 때는 보내지 않는다
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, func, insert, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.detection.rules import (
    KST,
    ROSTER_FIELDS,
    RuleError,
    applies_to_path,
    evaluation_facts,
    matches,
    uses_fields,
    validate_rule,
)
from app.models import (
    access_log,
    argus_user,
    detection,
    detection_batch_run,
    detection_log,
    detection_rule,
    detection_status_history,
    explanation,
    handler,
    source_system,
)

logger = logging.getLogger(__name__)

SELF_SOURCE = "ARGUS"  # Argus 자체 접속기록 — 룰 평가 대상 아님 (모듈 설명)
UNREGISTERED_NOTE = "취급자 명부에 없는 계정 — 퇴직 여부를 판정할 수 없어 탐지"
MAX_LOGS = 10_000  # 한 번에 처리할 최대 기록 수 — 밀려 있으면 쉬지 않고 다음 순찰 (2026-10-01 결정)
OPEN_STATUSES = ("DETECTED", "REQUESTED", "SUBMITTED", "REJECTED")
# 순찰은 한 번에 하나만 — worker가 여러 개 떠도 이 잠금을 잡은 쪽만 돈다
# (Spring ShedLock과 같은 역할)
BATCH_LOCK_KEY = 0x41524755534454  # 'ARGUSDT'

_LOG_COLUMNS = (
    access_log.c.id,
    access_log.c.source_system_id,
    access_log.c.access_path,
    access_log.c.actor_login_id,
    access_log.c.occurred_at,
    access_log.c.action,
    access_log.c.data_category,
    access_log.c.result,
    access_log.c.subject_count,
    source_system.c.code.label("source_code"),
)


@dataclass(frozen=True)
class BatchResult:
    run_id: int
    status: str
    from_id: int
    to_id: int
    processed: int = 0
    detected: int = 0
    error: str | None = None


def group_bucket(occurred_at: datetime) -> str:
    """탐지건 그룹의 날짜 — 한국 시각 기준. UTC 날짜로 묶으면 KST 새벽 0~9시 행위가 전날로 간다"""
    return occurred_at.astimezone(KST).date().isoformat()


def run_batch(engine: Engine, max_logs: int = MAX_LOGS) -> BatchResult | None:
    """순찰 1회. 다른 순찰이 진행 중이면 아무것도 하지 않고 None"""
    with engine.connect() as lock_conn:
        acquired = lock_conn.execute(select(func.pg_try_advisory_lock(BATCH_LOCK_KEY))).scalar_one()
        # 세션 단위 잠금은 커밋 후에도 유지된다 — 트랜잭션을 열어 둔 채 기다리지 않게
        lock_conn.commit()
        if not acquired:
            logger.info("another detection batch is running — skipped")
            return None
        try:
            _close_stale_runs(engine)
            return _run_locked(engine, max_logs)
        finally:
            # 세션 단위 잠금이라 커넥션이 풀로 돌아가기 전에 반드시 푼다
            lock_conn.execute(select(func.pg_advisory_unlock(BATCH_LOCK_KEY)))
            lock_conn.commit()


def _close_stale_runs(engine: Engine) -> None:
    # 잠금을 잡은 지금 RUNNING인 실행은 죽은 worker가 남긴 것이다
    with engine.begin() as conn:
        closed = conn.execute(
            update(detection_batch_run)
            .where(detection_batch_run.c.status == "RUNNING")
            .values(status="FAILED", finished_at=func.now(), error="worker restarted")
        ).rowcount
    if closed:
        logger.warning("closed %d stale RUNNING batch run(s)", closed)


def _run_locked(engine: Engine, max_logs: int) -> BatchResult:
    with engine.begin() as conn:
        last_to = conn.execute(
            select(func.coalesce(func.max(detection_batch_run.c.to_access_log_id), 0)).where(
                detection_batch_run.c.status == "SUCCESS"
            )
        ).scalar_one()
        max_id = conn.execute(select(func.coalesce(func.max(access_log.c.id), 0))).scalar_one()
        to_id = max(last_to, min(max_id, last_to + max_logs))
        # 새 기록이 없어도 실행 이력은 남긴다 — "탐지가 주기적으로 수행됐다"는 점검 증적 (§8②)
        run_id = conn.execute(
            insert(detection_batch_run)
            .values(from_access_log_id=last_to, to_access_log_id=to_id, status="RUNNING")
            .returning(detection_batch_run.c.id)
        ).scalar_one()

    try:
        with engine.begin() as conn:
            processed, detected = _evaluate(conn, run_id, last_to, to_id)
            conn.execute(
                update(detection_batch_run)
                .where(detection_batch_run.c.id == run_id)
                .values(
                    status="SUCCESS",
                    finished_at=func.now(),
                    processed_count=processed,
                    detected_count=detected,
                )
            )
    except Exception as error:
        # 예외 메시지 첫 줄만 — DB 오류의 DETAIL(둘째 줄 이후)에는 값이 실릴 수 있다
        message = f"{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ''}"
        with engine.begin() as conn:
            conn.execute(
                update(detection_batch_run)
                .where(detection_batch_run.c.id == run_id)
                .values(status="FAILED", finished_at=func.now(), error=message[:500])
            )
        logger.exception("detection batch #%d failed (range %d..%d]", run_id, last_to, to_id)
        return BatchResult(run_id, "FAILED", last_to, to_id, error=message[:500])

    logger.info(
        "detection batch #%d: range (%d..%d] processed=%d detected=%d",
        run_id,
        last_to,
        to_id,
        processed,
        detected,
    )
    return BatchResult(run_id, "SUCCESS", last_to, to_id, processed, detected)


def _evaluate(conn: Connection, run_id: int, from_id: int, to_id: int) -> tuple[int, int]:
    rules = (
        conn.execute(
            select(detection_rule).where(detection_rule.c.enabled).order_by(detection_rule.c.id)
        )
        .mappings()
        .all()
    )
    for rule in rules:
        try:
            validate_rule(rule)
        except RuleError as error:
            raise RuleError(f"rule #{rule['id']} is not evaluable: {error}") from None

    logs = (
        conn.execute(
            select(*_LOG_COLUMNS)
            .join(source_system, source_system.c.id == access_log.c.source_system_id)
            .where(access_log.c.id > from_id, access_log.c.id <= to_id)
            .order_by(access_log.c.id)
        )
        .mappings()
        .all()
    )
    # 순찰한 건수는 범위 전체(밀림 판단·실행 이력), 룰 평가는 감시 대상 시스템의 기록만
    targets = [log for log in logs if log["source_code"] != SELF_SOURCE]
    roster = _load_roster(conn, targets)
    facts = [evaluation_facts(log, roster) for log in targets]

    detected = 0
    for rule in rules:
        groups: dict[tuple[int, str, str], list[Any]] = defaultdict(list)
        for fact in facts:
            if applies_to_path(rule["access_path"], fact["access_path"]) and matches(
                rule["condition"], fact
            ):
                key = (
                    fact["source_system_id"],
                    fact["actor_login_id"],
                    group_bucket(fact["occurred_at"]),
                )
                groups[key].append(fact)
        for key, matched in groups.items():
            detected += _attach(conn, run_id, rule, key, matched)
    return len(logs), detected


def _load_roster(conn: Connection, logs: list[Any]) -> dict[tuple[int, str], datetime | None]:
    """이번 범위의 행위자들에 대한 명부 — (출처, 계정) → 퇴직 시각(재직 중이면 None)"""
    actors = {(log["source_system_id"], log["actor_login_id"]) for log in logs}
    if not actors:
        return {}
    rows = conn.execute(
        select(handler.c.source_system_id, handler.c.login_id, handler.c.terminated_at).where(
            tuple_(handler.c.source_system_id, handler.c.login_id).in_(actors)
        )
    )
    return {(r.source_system_id, r.login_id): r.terminated_at for r in rows}


def _attach(conn: Connection, run_id: int, rule: Any, key: tuple, logs: list[Any]) -> int:
    """그룹의 기록을 진행 중 탐지건에 붙인다. 새로 만들었으면 1"""
    source_system_id, actor, bucket = key
    ids = [log["id"] for log in logs]

    # 같은 룰로 이미 어떤 탐지건에 붙은 기록은 다시 붙이지 않는다 — 재처리해도 증거가 중복되지 않게
    already = set(
        conn.execute(
            select(detection_log.c.access_log_id)
            .join(detection, detection.c.id == detection_log.c.detection_id)
            .where(detection.c.rule_id == rule["id"], detection_log.c.access_log_id.in_(ids))
        ).scalars()
    )
    ids = [i for i in ids if i not in already]
    if not ids:
        return 0

    # 담당자가 이 건을 종결하는 순간(M4)과 겹치지 않게 잠그고 확인
    # — 그사이 종결됐으면 조건에서 빠져 새 건을 만든다
    detection_id = conn.execute(
        select(detection.c.id)
        .where(
            detection.c.rule_id == rule["id"],
            detection.c.source_system_id == source_system_id,
            detection.c.actor_login_id == actor,
            detection.c.group_bucket == bucket,
            detection.c.status.in_(OPEN_STATUSES),
        )
        .with_for_update()
    ).scalar_one_or_none()

    created = detection_id is None
    if created:
        first = min(log["occurred_at"] for log in logs)
        detection_id = conn.execute(
            insert(detection)
            .values(
                rule_id=rule["id"],
                rule_version=rule["version"],
                rule_snapshot=_rule_snapshot(rule),  # 룰이 바뀌어도 "왜 탐지됐는지"가 남는다
                source_system_id=source_system_id,
                actor_login_id=actor,
                group_bucket=bucket,
                severity=rule["severity"],
                first_occurred_at=first,
                last_occurred_at=first,
            )
            .returning(detection.c.id)
        ).scalar_one()
        conn.execute(
            insert(detection_status_history).values(
                detection_id=detection_id,
                from_status=None,
                to_status="DETECTED",
                round=0,
                actor_user_id=None,  # 시스템(탐지 배치)
                comment=_detected_comment(run_id, rule, logs),
            )
        )
        if rule["auto_request"] and _can_receive_request(conn, source_system_id, actor):
            _auto_request(conn, run_id, rule, detection_id)

    conn.execute(
        pg_insert(detection_log)
        .values([{"detection_id": detection_id, "access_log_id": i} for i in ids])
        .on_conflict_do_nothing()
    )
    _refresh_summary(conn, detection_id)
    return 1 if created else 0


def _detected_comment(run_id: int, rule: Any, logs: list[Any]) -> str:
    comment = f"탐지 배치 #{run_id}"
    # 그룹의 행위자는 하나라 명부 등록 여부도 같다 — 판정 불가로 탐지했다면 담당자가 알 수 있게
    if uses_fields(rule["condition"], ROSTER_FIELDS) and not logs[0]["actor_registered"]:
        comment += f" — {UNREGISTERED_NOTE}"
    return comment


def _can_receive_request(conn: Connection, source_system_id: int, actor: str) -> bool:
    """소명을 받을 사람이 있는가 — 재직 중인 취급자이고 A5 계정이 비활성(DISABLED)이 아님

    동기화 전이거나 퇴직으로 계정이 막혔으면 자동 요청을 보내지 않고 DETECTED로 남겨
    담당자가 처리한다(퇴직자 접속 등은 원래 담당자 사안). 잠김(LOCKED)·비밀번호 미설정은
    풀면 되는 일시 상태라 요청은 보낸다.
    """
    return (
        conn.execute(
            select(argus_user.c.id)
            .join(handler, handler.c.id == argus_user.c.handler_id)
            .where(
                handler.c.source_system_id == source_system_id,
                handler.c.login_id == actor,
                handler.c.employment_status == "ACTIVE",
                argus_user.c.role == "HANDLER",
                argus_user.c.status != "DISABLED",
            )
        ).first()
        is not None
    )


def _auto_request(conn: Connection, run_id: int, rule: Any, detection_id: int) -> None:
    """자동 소명 요청 (2026-10-01 사용자 확정) — 탐지 즉시 1차 요청, 요청자 = 시스템(NULL)

    담당자는 목록을 보고 오탐이면 요청을 취소(REQUESTED → DISMISSED)한다.
    하루에 같은 룰로 여러 번 걸려도 그룹핑(취급자·룰·KST 날짜)으로 탐지건·요청은 1개다.
    """
    conn.execute(
        update(detection).where(detection.c.id == detection_id).values(status="REQUESTED", round=1)
    )
    conn.execute(
        insert(explanation).values(
            detection_id=detection_id,
            round=1,
            requested_by=None,
            request_message=f"자동 소명 요청 — {rule['name']}",
        )
    )
    conn.execute(
        insert(detection_status_history).values(
            detection_id=detection_id,
            from_status="DETECTED",
            to_status="REQUESTED",
            round=1,
            actor_user_id=None,
            comment=f"자동 소명 요청 (탐지 배치 #{run_id})",
        )
    )


def _rule_snapshot(rule: Any) -> dict:
    keys = ("id", "name", "description", "rule_type", "access_path", "severity")
    snapshot = {k: rule[k] for k in keys}
    snapshot |= {
        "condition": rule["condition"],
        "aggregate": rule["aggregate"],
        "group_by": rule["group_by"],
        "auto_request": rule["auto_request"],
        "version": rule["version"],
    }
    return snapshot


_SUMMARY_SQL = text(
    """
    SELECT count(*)                                    AS log_count,
           coalesce(sum(a.subject_count), 0)           AS subject_count_sum,
           bool_or(a.subject_truncated)                AS subject_ids_truncated,
           array_agg(DISTINCT a.action ORDER BY a.action)               AS actions,
           array_agg(DISTINCT a.data_category ORDER BY a.data_category) AS data_categories,
           array_agg(DISTINCT host(a.client_ip) ORDER BY host(a.client_ip)) AS ip_list,
           min(a.occurred_at)                          AS first_occurred_at,
           max(a.occurred_at)                          AS last_occurred_at,
           (SELECT count(DISTINCT s)
              FROM detection_log dl2
              JOIN access_log a2 ON a2.id = dl2.access_log_id,
                   unnest(a2.subject_ids) AS s
             WHERE dl2.detection_id = :id)             AS distinct_subject_count
      FROM detection_log dl
      JOIN access_log a ON a.id = dl.access_log_id
     WHERE dl.detection_id = :id
    """
)


def _refresh_summary(conn: Connection, detection_id: int) -> None:
    """하위 로그 요약 — 원장 기록이 파기돼도 탐지건에 남는 판단 근거 (db-schema 3-4)

    회원 PK는 담지 않고 숫자만 담는다(최소처리).
    연결된 기록 전체에서 다시 계산하므로 재처리해도 같다.
    subject_ids가 1,000개에서 잘린 기록이 있으면 distinct_subject_count는 실제보다 작을 수 있어
    subject_ids_truncated로 "하한값"임을 표시한다 (2026-10-01 결정).
    """
    row = conn.execute(_SUMMARY_SQL, {"id": detection_id}).mappings().one()
    first, last = row["first_occurred_at"], row["last_occurred_at"]
    summary = {
        "log_count": row["log_count"],
        "subject_count_sum": row["subject_count_sum"],
        "distinct_subject_count": row["distinct_subject_count"],
        "subject_ids_truncated": bool(row["subject_ids_truncated"]),
        "actions": row["actions"],
        "data_categories": row["data_categories"],
        "ip_list": row["ip_list"],
        "first_occurred_at": first.astimezone(UTC).isoformat(),
        "last_occurred_at": last.astimezone(UTC).isoformat(),
    }
    conn.execute(
        update(detection)
        .where(detection.c.id == detection_id)
        .values(
            log_count=row["log_count"],
            first_occurred_at=first,
            last_occurred_at=last,
            log_summary=summary,
        )
    )
