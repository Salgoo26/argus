"""점검 보고서 집계 — 생성 시점의 스냅샷 (기능 레이어 9, LOG-09·§8②)

보고서는 "그때 무엇을 점검했고 결과가 어땠는지"의 증적이라, 만든 순간의 숫자를 그대로 저장한다
(inspection_report.summary). 나중에 탐지건 상태가 바뀌어도 보고서 내용은 바뀌지 않는다.

- **경로별로 따로 집계**한다 — 화면 경유(3티어)·DB 직접(2티어)을 합산 수치로만 보여 주지 않는다
  (policy 1-5). 범위를 한 경로로 고르면 그 경로 섹션만 담는다(2026-10-07 사용자 결정)
- 정보주체 식별값은 **마스킹 값만**, 탐지건마다 앞 몇 개 + "외 N명"
  (policy 6-1, 2026-10-07 사용자 결정).
  DB 직접 기록 중 결과에 회원을 가리키는 열이 없던 조회는 "미특정 N건"으로 표시된다(v0.1 보강 G-1)
- 감시 대상(플랫폼) 기록만 집계한다. Argus 자체 접속기록은 점검 행위의 기록이라 섹션에 섞지 않는다
- 탐지건마다 처리 내용을 싣는다(v0.1 보강 B): 마지막 차수 소명 요지·검토 결과와 의견·관련 티켓·
  첨부 개수, 처리 담당자·일시, 요청 취소 사유. 자유 입력은 앞 200자만
- 무결성에 원장 마지막 id·해시·건수와 직전 보고서 대조 결과를 싣는다(v0.1 보강 I).
  보고서도 같은 DB에
  있으므로 출력해 결재문서(관리대장)에 붙이는 운영이 전제다
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, and_, func, or_, select

from app.detections.masking import mask_subject
from app.ledger.hashchain import verify_chain
from app.models import (
    access_log,
    argus_user,
    detection,
    detection_batch_run,
    detection_log,
    detection_status_history,
    explanation,
    explanation_attachment,
    handler,
    inspection_report,
    source_system,
)

PLATFORM = "PLATFORM"
PATHS = ("APP", "DB")
CASES_MAX = 50  # 보고서에 싣는 탐지건 목록 상한 — 심각도 높은 순
SUBJECT_PREVIEW = 3  # 탐지건마다 보여 주는 마스킹 식별값 수 ("외 N명")
EXCERPT_CHARS = 200  # 소명·검토 의견·취소 사유 요지 길이 (v0.1 보강 B 【기본값】)
_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def build_summary(conn: Connection, start: datetime, end: datetime, scope: str) -> dict[str, Any]:
    paths = PATHS if scope == "ALL" else (scope,)
    platform_id = conn.execute(
        select(source_system.c.id).where(source_system.c.code == PLATFORM)
    ).scalar_one()
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "scope": scope,
        "integrity": _integrity(conn),
        # §8② 점검 수행 — 기간 중 탐지 배치(자동 점검)가 실제로 돌았는지
        "patrol": _patrol(conn, start, end),
        "paths": {path: _section(conn, platform_id, path, start, end) for path in paths},
    }


def _integrity(conn: Connection) -> dict[str, Any]:
    """§8③ 위·변조 점검 (안내서 95 "위·변조 확인 정보를 별도 저장매체 또는 관리대장에")
    - 보고서를 만들 때 원장 전체의 해시체인을 다시 계산한 결과
    - 원장의 **마지막 id·해시·전체 건수** — 출력한 보고서(관리대장)가 DB 밖의 기준점이 된다
    - **직전 보고서의 마지막 행**이 같은 해시로 남아 있는지 — 끝부분을 지우면 남은 체인은 그대로
      이어져 해시체인 검증만으로는 못 잡는다(v0.1 보강 I, 갭 A16)
    """
    a = access_log.c
    chain = verify_chain(conn)
    total, last_id = conn.execute(select(func.count(), func.max(a.id))).one()
    last_hash = (
        conn.execute(select(a.hash).where(a.id == last_id)).scalar_one().strip()
        if last_id is not None
        else None
    )
    return {
        "ok": chain.ok,
        "checked": chain.checked,
        "broken_at": chain.broken_at_id,
        "total": total,
        "last_id": last_id,
        "last_hash": last_hash,
        "previous": _previous_anchor(conn),
    }


def _previous_anchor(conn: Connection) -> dict[str, Any]:
    """직전 보고서(마지막 id를 남긴 것 중 가장 최근)의 마지막 행 대조
    MATCH = 같은 해시로 남아 있음 / MISMATCH = 없거나 해시가 다름 / NONE = 비교할 보고서 없음"""
    r = inspection_report.c
    anchor_id = r.summary["integrity"]["last_id"].as_integer()
    previous = conn.execute(
        select(r.id, anchor_id.label("last_id"), r.summary["integrity"]["last_hash"].as_string())
        .where(anchor_id.is_not(None))
        .order_by(r.id.desc())
        .limit(1)
    ).first()
    if previous is None:
        return {"status": "NONE", "report_id": None, "last_id": None}
    report_id, last_id, last_hash = previous
    current = conn.execute(
        select(access_log.c.hash).where(access_log.c.id == last_id)
    ).scalar_one_or_none()
    matched = current is not None and current.strip() == last_hash
    return {
        "status": "MATCH" if matched else "MISMATCH",
        "report_id": report_id,
        "last_id": last_id,
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
            # 기한 초과 (v0.1 보강 F-3) — 기한 뒤에 제출했거나, 보고서를 만드는 지금 기한이 지났는데
            # 아직 제출 전인 차수
            func.count().filter(
                or_(
                    e.submitted_at > e.due_at,
                    and_(e.submitted_at.is_(None), e.due_at < func.now()),
                )
            ),
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
            # 처리한 정보주체를 특정하지 못한 기록 (DB 직접 — 결과에 회원 열이 없던 조회)
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
            "overdue": explanations[4],
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
                # 어떻게 처리했는지 (v0.1 보강 B — 실무 결재문서처럼 사유까지 남긴다)
                "explanation": _latest_explanation(conn, row["id"]),
                **_handled(conn, row["id"]),
                "close_reason": (
                    excerpt(row["close_reason"]) if row["status"] == "DISMISSED" else None
                ),
            }
        )
    return result


def excerpt(text: str | None) -> str | None:
    """자유 입력 요지 — 앞 EXCERPT_CHARS자, 넘으면 "…". 자동 마스킹은 하지 않는다(오탐·누락이 커서)
    — 소명 제출 화면에서 개인정보를 적지 말라고 안내한다"""
    if text is None:
        return None
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + "…"


def _latest_explanation(conn: Connection, detection_id: int) -> dict[str, Any] | None:
    """마지막 차수의 소명 — 요청만 있고 아직 제출 전이면 내용·첨부는 비어 있다"""
    e = explanation.c
    row = (
        conn.execute(
            select(explanation)
            .where(e.detection_id == detection_id)
            .order_by(e.round.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    submitted = row["submitted_at"] is not None
    attachments = (
        conn.execute(
            select(func.count()).where(explanation_attachment.c.explanation_id == row["id"])
        ).scalar_one()
        if submitted  # 고치는 중인 초안의 첨부는 담당자에게도 보이지 않는다 — 보고서도 같다
        else 0
    )
    return {
        "round": row["round"],
        "content": excerpt(row["content"]) if submitted else None,
        "submitted_at": row["submitted_at"].isoformat() if submitted else None,
        "review_result": row["review_result"],
        "review_comment": excerpt(row["review_comment"]),
        "ticket_ids": list(row["ticket_ids"] or []) if submitted else [],
        "attachment_count": attachments,
    }


def _handled(conn: Connection, detection_id: int) -> dict[str, Any]:
    """처리 담당자·일시 = 담당자가 마지막으로 상태를 바꾼 기록 (시스템 요청·취급자 제출 제외)"""
    h = detection_status_history.c
    row = conn.execute(
        select(argus_user.c.login_id, h.created_at)
        .join(argus_user, argus_user.c.id == h.actor_user_id)
        .where(h.detection_id == detection_id, argus_user.c.role == "OFFICER")
        .order_by(h.id.desc())
        .limit(1)
    ).first()
    return {
        "handled_by": row.login_id if row else None,
        "handled_at": row.created_at.isoformat() if row else None,
    }


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
