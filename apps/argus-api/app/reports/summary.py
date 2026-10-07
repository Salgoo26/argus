"""점검 보고서 집계 — 생성 시점의 스냅샷 (기능 레이어 9, LOG-09·§8②)

보고서는 "그때 무엇을 점검했고 결과가 어땠는지"의 증적이라, 만든 순간의 숫자를 그대로 저장한다
(inspection_report.summary). 나중에 탐지건 상태가 바뀌어도 보고서 내용은 바뀌지 않는다.

- **경로별로 따로 집계**한다 — 화면 경유(3티어)·DB 직접(2티어)을 합산 수치로만 보여 주지 않는다
  (policy 1-5). 범위를 한 경로로 고르면 그 경로 섹션만 담는다(2026-10-07 사용자 결정)
- 정보주체 식별값은 **마스킹 값만**, 탐지건마다 앞 몇 개 + "외 N명"
  (policy 6-1, 2026-10-07 사용자 결정).
  DB 직접 기록은 회원번호 추출 보류로 식별값이 없어 "미특정 N건"으로 표시된다
- 감시 대상(플랫폼) 기록만 집계한다. Argus 자체 접속기록은 점검 행위의 기록이라 섹션에 섞지 않는다
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, and_, func, select

from app.detections.masking import mask_subject
from app.ledger.hashchain import verify_chain
from app.models import (
    access_log,
    detection,
    detection_batch_run,
    detection_log,
    explanation,
    handler,
    source_system,
)

PLATFORM = "PLATFORM"
PATHS = ("APP", "DB")
CASES_MAX = 50  # 보고서에 싣는 탐지건 목록 상한 — 심각도 높은 순
SUBJECT_PREVIEW = 3  # 탐지건마다 보여 주는 마스킹 식별값 수 ("외 N명")
_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def build_summary(conn: Connection, start: datetime, end: datetime, scope: str) -> dict[str, Any]:
    paths = PATHS if scope == "ALL" else (scope,)
    platform_id = conn.execute(
        select(source_system.c.id).where(source_system.c.code == PLATFORM)
    ).scalar_one()
    chain = verify_chain(conn)
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "scope": scope,
        # §8③ 위·변조 점검 — 보고서를 만들 때 원장 전체의 해시체인을 다시 계산한 결과
        "integrity": {"ok": chain.ok, "checked": chain.checked, "broken_at": chain.broken_at_id},
        # §8② 점검 수행 — 기간 중 탐지 배치(자동 점검)가 실제로 돌았는지
        "patrol": _patrol(conn, start, end),
        "paths": {path: _section(conn, platform_id, path, start, end) for path in paths},
    }


def _patrol(conn: Connection, start: datetime, end: datetime) -> dict[str, Any]:
    run = detection_batch_run.c
    in_period = and_(run.started_at >= start, run.started_at < end)
    counts = dict(
        conn.execute(select(run.status, func.count()).where(in_period).group_by(run.status)).all()
    )
    last = conn.execute(
        select(func.max(run.finished_at)).where(in_period, run.status == "SUCCESS")
    ).scalar_one()
    return {
        "runs": sum(counts.values()),
        "success": counts.get("SUCCESS", 0),
        "failed": counts.get("FAILED", 0),
        "last_success_at": last.isoformat() if last else None,
    }


def _grouped(conn: Connection, column, where) -> dict[str, int]:
    rows = conn.execute(select(column, func.count()).where(*where).group_by(column)).all()
    return {str(key): count for key, count in rows}


def _section(
    conn: Connection, platform_id: int, path: str, start: datetime, end: datetime
) -> dict[str, Any]:
    a, d, e = access_log.c, detection.c, explanation.c
    logs = (
        a.source_system_id == platform_id,
        a.access_path == path,
        a.occurred_at >= start,
        a.occurred_at < end,
    )
    total, actors, failures = conn.execute(
        select(
            func.count(),
            func.count(func.distinct(a.actor_login_id)),
            func.count().filter(a.result == "FAILURE"),
        ).where(*logs)
    ).one()
    unresolved = conn.execute(
        select(func.count()).where(*logs, a.context["subject_unresolved"].as_boolean())
    ).scalar_one()

    cases = (
        d.source_system_id == platform_id,
        d.access_path == path,
        d.detected_at >= start,
        d.detected_at < end,
    )
    by_rule = conn.execute(
        select(d.rule_snapshot["name"].as_string().label("name"), func.count())
        .where(*cases)
        .group_by("name")
        .order_by(func.count().desc())
    ).all()
    explanations = conn.execute(
        select(
            func.count(),
            func.count(e.submitted_at),
            func.count().filter(e.review_result == "APPROVED"),
            func.count().filter(e.review_result == "REJECTED"),
        )
        .select_from(explanation.join(detection, detection.c.id == e.detection_id))
        .where(*cases)
    ).one()
    by_status = _grouped(conn, d.status, cases)

    return {
        "logs": {
            "total": total,
            "actors": actors,
            "failures": failures,
            "by_action": _grouped(conn, a.action, logs),
            "by_category": _grouped(conn, a.data_category, logs),
            # 처리한 정보주체를 특정하지 못한 기록 (DB 직접 — 회원번호 추출 보류)
            "subject_unresolved": unresolved,
        },
        "detections": {
            "total": sum(by_status.values()),
            "by_severity": _grouped(conn, d.severity, cases),
            "by_status": by_status,
            "by_rule": [{"name": name, "count": count} for name, count in by_rule],
            "escalated": by_status.get("ESCALATED", 0),
        },
        "explanations": {
            "requested": explanations[0],
            "submitted": explanations[1],
            "approved": explanations[2],
            "rejected": explanations[3],
        },
        "cases": _cases(conn, cases),
    }


def _cases(conn: Connection, where) -> list[dict[str, Any]]:
    d = detection.c
    actor_name = (
        select(handler.c.name)
        .where(
            handler.c.source_system_id == d.source_system_id,
            handler.c.login_id == d.actor_login_id,
        )
        .scalar_subquery()
    )
    rows = (
        conn.execute(select(detection, actor_name.label("actor_name")).where(*where))
        .mappings()
        .all()
    )
    rows = sorted(rows, key=lambda r: (_SEVERITY_ORDER.get(r["severity"], 9), r["detected_at"]))
    result = []
    for row in rows[:CASES_MAX]:
        summary = row["log_summary"] or {}
        result.append(
            {
                "id": row["id"],
                "rule_name": row["rule_snapshot"]["name"],
                "severity": row["severity"],
                "status": row["status"],
                "round": row["round"],
                "actor_login_id": row["actor_login_id"],
                "actor_name": row["actor_name"],
                "first_occurred_at": row["first_occurred_at"].isoformat(),
                "log_count": row["log_count"],
                "subject_count_sum": summary.get("subject_count_sum") or 0,
                "distinct_subject_count": summary.get("distinct_subject_count") or 0,
                # 원본 식별값은 싣지 않는다 — 마스킹 값 앞 몇 개만 (policy 6-1)
                "subjects": _masked_subjects(conn, row["id"]),
            }
        )
    return result


def _masked_subjects(conn: Connection, detection_id: int) -> list[str]:
    a = access_log.c
    ids = (
        select(func.unnest(a.subject_ids).label("subject_id"), a.subject_type)
        .join(detection_log, detection_log.c.access_log_id == a.id)
        .where(detection_log.c.detection_id == detection_id)
        .subquery()
    )
    rows = conn.execute(
        select(ids.c.subject_type, ids.c.subject_id)
        .distinct()
        .order_by(ids.c.subject_id)
        .limit(SUBJECT_PREVIEW)
    ).all()
    return [mask_subject(subject_type, subject_id) for subject_type, subject_id in rows]


def masked_count(summary: dict[str, Any]) -> int:
    """보고서에 실린 마스킹 식별값 수 — Argus 자체 접속기록의 처리 건수 (policy 6-3)"""
    return sum(len(c["subjects"]) for s in summary["paths"].values() for c in s["cases"])
