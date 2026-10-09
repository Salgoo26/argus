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
- 키 = (룰, 출처, 접근 경로, 취급자, 발생 날짜)
  + **EVENT 룰은 데이터 유형·행위 구분**(조회/내려받기/변경·삭제/로그인·로그아웃, v0.1 보강 J-2)
  — 같은 날 회원정보 조회와 결제정보 조회는 다른 탐지건. AGGREGATE는 그대로(집계 의미가 바뀜)
  — 날짜는 **한국 시각(KST)** 기준 (2026-10-01 결정)
- **탐지건 하나 = 경로 하나**: 적용 경로가 "전체"인 룰도 화면 경유(APP)·DB 직접(DB) 기록을
  한 탐지건에 섞지 않는다 (policy 1-5, 2026-10-06 — 두 경로는 성격이 다른 기록이고 보고도 따로)
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

AGGREGATE 룰 (기능 레이어 4 — aggregate.py):
- 범위의 기록이 속한 (취급자, 경로, 윈도우)를 골라 그 윈도우를 원장에서 통째로 다시 집계한다
  — 경로별로 따로 집계하므로 전월 기준선도 같은 경로 안에서만 잡힌다 (policy 1-5)
- 그룹 키의 날짜 자리에 윈도우 시작 시각(KST)을 쓴다 — 집계 단위가 곧 탐지건 (policy 2-2)
- 진행 중 건이 있으면 새 기록을 붙이고 집계값을 갱신, **이미 종결된 윈도우는 다시 탐지하지 않는다**
  (EVENT의 "종결 후엔 새 건"과 다르다 — 집계는 누적이라 순찰마다 새 건이 생김)
- 전월 동기 비율 룰은 전월 기록이 없으면 판정하지 않는다(비율을 정할 수 없음)

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

from app.detection.aggregate import (
    bucket_label,
    measure,
    most_repeated_subject,
    prev_month_same_period,
    window_end,
    window_start,
)
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
from app.detections.masking import mask_subject
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
from app.notifications.events import due_at, on_detected, on_requested

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
    access_log.c.client_ip,  # 접속지 조건·고유 접속지 수 (v0.1 보강 K)
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


# 행위 구분 (v0.1 보강 J-2) — 같은 성격의 행위끼리만 한 탐지건으로 묶는다
ACTION_GROUPS = {
    "READ": "READ",
    "DOWNLOAD": "DOWNLOAD",
    "EXPORT": "DOWNLOAD",
    "CREATE": "CHANGE",
    "UPDATE": "CHANGE",
    "DELETE": "CHANGE",
    "LOGIN": "SESSION",
    "LOGOUT": "SESSION",
}


def action_group(action: str) -> str:
    """조회 / 내려받기 / 변경·삭제 / 로그인·로그아웃"""
    return ACTION_GROUPS[action]


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
        if rule["rule_type"] == "AGGREGATE":
            detected += _evaluate_aggregate(conn, run_id, rule, facts, roster, to_id)
            continue
        groups: dict[tuple, list[Any]] = defaultdict(list)
        for fact in facts:
            if _hits(rule, fact):
                key = (
                    fact["source_system_id"],
                    fact["access_path"],
                    fact["actor_login_id"],
                    group_bucket(fact["occurred_at"]),
                    # 다른 성격의 처리는 다른 탐지건 — 소명 하나가 다른 행위를 덮지 않게 (J-2)
                    fact["data_category"],
                    action_group(fact["action"]),
                )
                groups[key].append(fact)
        for key, matched in groups.items():
            detected += _attach(conn, run_id, rule, key, matched)
    return len(logs), detected


def _hits(rule: Any, fact: dict[str, Any]) -> bool:
    return applies_to_path(rule["access_path"], fact["access_path"]) and matches(
        rule["condition"], fact
    )


def _evaluate_aggregate(
    conn: Connection,
    run_id: int,
    rule: Any,
    facts: list[dict[str, Any]],
    roster: dict[tuple[int, str], datetime | None],
    to_id: int,
) -> int:
    """이번 범위의 기록이 속한 윈도우들을 **윈도우 전체로** 다시 집계한다 (db-schema 3-7 #4)

    이번 순찰에 새로 들어온 기록만 세면 윈도우가 순찰 경계에서 쪼개진다 — 그래서 범위의 기록은
    "어느 윈도우를 다시 볼지" 고르는 데만 쓰고, 집계는 원장에서 그 윈도우를 통째로 읽어 한다.
    """
    spec = rule["aggregate"]
    windows = {
        (
            f["source_system_id"],
            f["access_path"],
            f["actor_login_id"],
            window_start(f["occurred_at"], spec["window"]),
        )
        for f in facts
        if _hits(rule, f)
    }
    detected = 0
    for source_system_id, path, actor, start in sorted(windows):
        end = window_end(start, spec["window"])
        scope = (source_system_id, path, actor)
        logs = _window_logs(conn, rule, roster, scope, start, end, to_id)
        value = measure(logs, spec["measure"])
        if spec["compare"] == "ABSOLUTE":
            note = f"집계 {value} ≥ 기준 {spec['threshold']}" + _top_subject_note(logs, spec)
            exceeded, aggregate_value = value >= spec["threshold"], value
        else:  # RATIO_TO_BASELINE — 전월 동기 대비
            as_of = min(datetime.now(UTC), end)
            b_start, b_end = prev_month_same_period(start, as_of)
            base = measure(
                _window_logs(conn, rule, roster, scope, b_start, b_end, to_id),
                spec["measure"],
            )
            if base < spec.get("min_baseline", 1):
                # 기준선이 없거나 너무 작으면 비율을 믿을 수 없다 — 신규 취급자·첫 달·월초는
                # 판정하지 않는다 (대량 조회 같은 절대 기준 룰이 따로 본다)
                continue
            ratio = value / base
            note = f"당월 {value} / 전월 동기 {base} = {ratio:.2f}배 ≥ {spec['threshold']}배"
            note += _top_subject_note(logs, spec)
            exceeded, aggregate_value = ratio >= spec["threshold"], round(ratio, 4)
        if exceeded and logs:
            key = (source_system_id, path, actor, bucket_label(start), None, None)
            detected += _attach(conn, run_id, rule, key, logs, aggregate_value, note)
    return detected


# 회원번호를 읽어야 하는 집계 (DISTINCT_SUBJECT·MAX_SUBJECT_REPEAT)
SUBJECT_MEASURES = frozenset({"DISTINCT_SUBJECT", "MAX_SUBJECT_REPEAT"})


def _top_subject_note(logs: list[dict[str, Any]], spec: Any) -> str:
    """특정 회원 반복 처리 — 어느 회원인지 **마스킹 식별값**으로 탐지 이력에 (v0.1 보강 K-1)"""
    if spec["measure"] != "MAX_SUBJECT_REPEAT":
        return ""
    top = most_repeated_subject(logs)
    if top is None:
        return ""
    (subject_type, subject), count = top
    return f" — 최다 처리 {mask_subject(subject_type, subject)} {count}회"


def _window_logs(
    conn: Connection,
    rule: Any,
    roster: dict[tuple[int, str], datetime | None],
    scope: tuple[int, str, str],
    start: datetime,
    end: datetime,
    to_id: int,
) -> list[dict[str, Any]]:
    """한 취급자·한 경로의 [start, end) 기록 중 룰에 맞는 것 — 이번 순찰 범위(id ≤ to_id)까지"""
    source_system_id, path, actor = scope
    columns = list(_LOG_COLUMNS)
    if rule["aggregate"]["measure"] in SUBJECT_MEASURES:
        columns += [access_log.c.subject_ids, access_log.c.subject_type]
    rows = (
        conn.execute(
            select(*columns)
            .join(source_system, source_system.c.id == access_log.c.source_system_id)
            .where(
                access_log.c.source_system_id == source_system_id,
                access_log.c.access_path == path,
                access_log.c.actor_login_id == actor,
                access_log.c.occurred_at >= start,
                access_log.c.occurred_at < end,
                access_log.c.id <= to_id,
            )
            .order_by(access_log.c.id)
        )
        .mappings()
        .all()
    )
    facts = (evaluation_facts(row, roster) for row in rows)
    return [f for f in facts if _hits(rule, f)]


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


def _attach(
    conn: Connection,
    run_id: int,
    rule: Any,
    key: tuple,
    logs: list[Any],
    aggregate_value: float | None = None,
    note: str | None = None,
) -> int:
    """그룹의 기록을 진행 중 탐지건에 붙인다. 새로 만들었으면 1

    AGGREGATE는 집계값(aggregate_value)을 함께 갱신하고, note(집계 근거)를 탐지 이력에 남긴다.
    """
    source_system_id, path, actor, bucket, data_category, action_group_ = key
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
            detection.c.access_path == path,
            detection.c.actor_login_id == actor,
            detection.c.group_bucket == bucket,
            detection.c.data_category.is_not_distinct_from(data_category),
            detection.c.action_group.is_not_distinct_from(action_group_),
            detection.c.status.in_(OPEN_STATUSES),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if (
        detection_id is None
        and rule["rule_type"] == "AGGREGATE"
        and _window_was_judged(conn, rule, key)
    ):
        # 집계 윈도우는 한 번만 판단한다 — 종결된 윈도우에 기록이 더 쌓여도 새 건을 만들지 않는다.
        # 집계는 누적이라 승인 뒤에도 같은 윈도우가 계속 기준을 넘어, 순찰마다 새 건이 생기기 때문
        # (2026-10-01, 사용자 확인 대기 — 구현 로그 설계 변경)
        return 0

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
                access_path=path,
                actor_login_id=actor,
                group_bucket=bucket,
                data_category=data_category,
                action_group=action_group_,
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
                comment=_detected_comment(run_id, rule, logs, note),
            )
        )
        on_detected(conn, detection_id, rule["severity"])  # 상이면 담당자 화면 알림 (F-1)
        if rule["auto_request"] and _can_receive_request(conn, source_system_id, actor):
            _auto_request(conn, run_id, rule, detection_id)

    conn.execute(
        pg_insert(detection_log)
        .values([{"detection_id": detection_id, "access_log_id": i} for i in ids])
        .on_conflict_do_nothing()
    )
    if aggregate_value is not None:
        conn.execute(
            update(detection)
            .where(detection.c.id == detection_id)
            .values(aggregate_value=aggregate_value)
        )
    _refresh_summary(conn, detection_id)
    return 1 if created else 0


def _window_was_judged(conn: Connection, rule: Any, key: tuple) -> bool:
    source_system_id, path, actor, bucket, *_ = key  # 집계 윈도우 — 성격 구분 없음
    return (
        conn.execute(
            select(detection.c.id).where(
                detection.c.rule_id == rule["id"],
                detection.c.source_system_id == source_system_id,
                detection.c.access_path == path,
                detection.c.actor_login_id == actor,
                detection.c.group_bucket == bucket,
            )
        ).first()
        is not None
    )


def _detected_comment(run_id: int, rule: Any, logs: list[Any], note: str | None = None) -> str:
    comment = f"탐지 배치 #{run_id}"
    if note:
        comment += f" — {note}"
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
            due_at=due_at(conn),  # 소명 기한 (F-3)
        )
    )
    on_requested(conn, detection_id, rule["severity"], 1)  # 취급자 화면 알림 (F-1)
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
