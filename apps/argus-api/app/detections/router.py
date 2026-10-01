"""탐지건 조회·소명 흐름 API (LOG-05~08, actor-flows F-05·F-06)

조회
- GET /api/detections                 목록
    담당자: 전체 / 취급자: 본인 건 중 소명 요청을 받은 건(round ≥ 1)
- GET /api/detections/{detection_id}  상세
    하위 접속기록(정보주체 마스킹), 요약, 차수별 소명, 상태 이력

상태 전이 (transitions.py의 표에 있는 것만)
- POST .../request   담당자: 소명 요청 / 재요청(차수 +1)
- POST .../dismiss   담당자: 소명 불요 / 요청 취소(오탐) — 사유 필수
- POST .../submit    취급자 본인: 소명 제출 — 내용 필수
- POST .../approve   담당자: 승인(종결)
- POST .../reject    담당자: 반려 — 사유 필수
- POST .../escalate  담당자: 에스컬레이션(종결)

접근 통제
- 취급자에게 보이지 않는 건(남의 건, 아직 요청 전인 건)은 403이 아니라 **404**
  — 존재 여부가 드러나지 않게
- 취급자 노출 범위를 "요청받은 건"으로 둔 이유:
  자동 요청이 가지 않은 DETECTED 건은 담당자의 검토 대기함이다
  (2026-10-01 결정, db-schema 3-1 "A5는 자신의 탐지건만 — 애플리케이션 계층에서 강제")

접속기록(LOG-17)
- 조회는 READ(ACCESS_LOG)로 기록하되 **건수만** — 화면에 보여준(마스킹된) 식별값의 수 (policy 6-3)
- 상태 전이는 접속기록이 아니라 **상태 이력(detection_status_history)**에 누가·언제·사유를 남긴다
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy import Connection, and_, func, insert, select, update

from app.agent import access_log, access_log_exempt, record_subject_count
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detections.masking import mask_subject
from app.detections.transitions import ACTION_ROLES, HANDLER, TRANSITIONS
from app.errors import ApiError
from app.models import access_log as access_log_table
from app.models import (
    argus_user,
    detection,
    detection_log,
    detection_status_history,
    explanation,
    explanation_attachment,
    handler,
)

router = APIRouter(prefix="/api/detections")

STATUSES = ("DETECTED", "REQUESTED", "SUBMITTED", "APPROVED", "REJECTED", "DISMISSED", "ESCALATED")
_EXEMPT_TRANSITION = "상태 변경은 detection_status_history에 누가·언제·사유를 기록 (api-spec 2-7)"

RequiredText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "detection not found")


def _visible(query, user: AuthenticatedUser):
    """역할별로 볼 수 있는 탐지건만 남긴다 — 모든 조회·전이가 이 필터를 거친다"""
    if user.role != HANDLER:
        return query
    # 취급자 본인 = 연결된 명부의 (출처, 계정) — 접속기록·탐지건의 행위자 매칭 키와 같다
    own = handler.alias("own")
    return query.where(
        select(own.c.id)
        .where(
            own.c.id == user.handler_id,
            own.c.source_system_id == detection.c.source_system_id,
            own.c.login_id == detection.c.actor_login_id,
        )
        .exists(),
        detection.c.round >= 1,
    )


_actor_name = select(handler.c.name).where(
    and_(
        handler.c.source_system_id == detection.c.source_system_id,
        handler.c.login_id == detection.c.actor_login_id,
    )
)


def _case_summary(row) -> dict:
    summary = row["log_summary"] or {}
    return {
        "id": row["id"],
        "rule_name": row["rule_snapshot"]["name"],
        "severity": row["severity"],
        "actor_login_id": row["actor_login_id"],
        "actor_name": row["actor_name"],
        "group_bucket": row["group_bucket"],
        "status": row["status"],
        "round": row["round"],
        "log_count": row["log_count"],
        # AGGREGATE 집계값 — 절대 기준이면 건수, 전월 대비면 배율 (EVENT는 null)
        "aggregate_value": (
            float(row["aggregate_value"]) if row["aggregate_value"] is not None else None
        ),
        "subject_count_sum": summary.get("subject_count_sum"),
        "distinct_subject_count": summary.get("distinct_subject_count"),
        "first_occurred_at": row["first_occurred_at"],
        "last_occurred_at": row["last_occurred_at"],
        "detected_at": row["detected_at"],
        "closed_at": row["closed_at"],
    }


def _case_query():
    return select(detection, _actor_name.scalar_subquery().label("actor_name"))


# ── 조회 ──────────────────────────────────────────────────


@router.get("")
@access_log(action="READ", data_category="ACCESS_LOG")
def list_detections(
    request: Request,
    user: CurrentUser,
    status: Annotated[str | None, Query(pattern="^(" + "|".join(STATUSES) + ")$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    query = _visible(_case_query(), user)
    if status is not None:
        query = query.where(detection.c.status == status)
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = conn.execute(
            query.order_by(detection.c.detected_at.desc(), detection.c.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [_case_summary(r) for r in rows]
    record_subject_count(0)  # 목록에는 정보주체 식별값이 없다 (건수 합계만)
    return {"items": items, "page": page, "size": size, "total": total}


@router.get("/{detection_id}")
@access_log(action="READ", data_category="ACCESS_LOG")
def get_detection(detection_id: int, request: Request, user: CurrentUser) -> dict:
    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(_visible(_case_query(), user).where(detection.c.id == detection_id))
            .mappings()
            .first()
        )
        if row is None:
            raise _not_found()
        logs = _logs(conn, detection_id)
        explanations = _explanations(conn, detection_id, user)
        history = _history(conn, detection_id)

    record_subject_count(sum(len(log["subjects"]) for log in logs))
    snapshot = row["rule_snapshot"]
    return _case_summary(row) | {
        "rule": {
            "name": snapshot["name"],
            "description": snapshot.get("description"),
            "severity": snapshot["severity"],
            "rule_type": snapshot["rule_type"],
            "condition": snapshot["condition"],
            "aggregate": snapshot.get("aggregate"),
            "version": snapshot["version"],
        },
        "log_summary": row["log_summary"],
        "close_reason": row["close_reason"],
        "logs": logs,
        "explanations": explanations,
        "history": history,
    }


def _logs(conn: Connection, detection_id: int) -> list[dict]:
    a = access_log_table
    rows = conn.execute(
        select(
            a.c.id,
            a.c.occurred_at,
            a.c.action,
            a.c.data_category,
            a.c.result,
            a.c.client_ip,
            a.c.request_method,
            a.c.request_path,
            a.c.request_query_keys,
            a.c.subject_type,
            a.c.subject_ids,
            a.c.subject_count,
            a.c.subject_truncated,
            a.c.context,
        )
        .join(detection_log, detection_log.c.access_log_id == a.c.id)
        .where(detection_log.c.detection_id == detection_id)
        .order_by(a.c.id)
    ).mappings()
    return [
        {
            "access_log_id": r["id"],
            "occurred_at": r["occurred_at"],
            "action": r["action"],
            "data_category": r["data_category"],
            "result": r["result"],
            "client_ip": str(r["client_ip"]),
            "request_method": r["request_method"],
            "request_path": r["request_path"],
            "request_query_keys": r["request_query_keys"],
            "subject_count": r["subject_count"],
            "subject_truncated": r["subject_truncated"],
            # 원본 식별값은 응답에 싣지 않는다 — 마스킹된 표시값만 (LOG-10)
            "subjects": [mask_subject(r["subject_type"], s) for s in r["subject_ids"] or []],
            # 업무 근거 티켓(1:1 문의 등) — 소명과 대조할 단서. 다른 context 키는 싣지 않음
            "ticket_id": (r["context"] or {}).get("ticket_id"),
        }
        for r in rows
    ]


def _attachments_by_explanation(conn: Connection, explanation_ids: list[int]) -> dict:
    """차수별 첨부 목록 (메타데이터·해시만 — 파일은 다운로드 API로, 기능 레이어 7 ③)"""
    if not explanation_ids:
        return {}
    rows = conn.execute(
        select(explanation_attachment)
        .where(explanation_attachment.c.explanation_id.in_(explanation_ids))
        .order_by(explanation_attachment.c.id)
    ).mappings()
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["explanation_id"], []).append(
            {
                "id": r["id"],
                "original_name": r["original_name"],
                "content_type": r["content_type"],
                "size_bytes": r["size_bytes"],
                "sha256": r["sha256"],
                "uploaded_at": r["uploaded_at"],
            }
        )
    return grouped


def _explanations(conn: Connection, detection_id: int, user: AuthenticatedUser) -> list[dict]:
    requester, submitter, reviewer = (
        argus_user.alias(n) for n in ("requester", "submitter", "reviewer")
    )
    rows = (
        conn.execute(
            select(
                explanation,
                requester.c.login_id.label("requested_by_login"),
                submitter.c.login_id.label("submitted_by_login"),
                reviewer.c.login_id.label("reviewed_by_login"),
            )
            .outerjoin(requester, requester.c.id == explanation.c.requested_by)
            .outerjoin(submitter, submitter.c.id == explanation.c.submitted_by)
            .outerjoin(reviewer, reviewer.c.id == explanation.c.reviewed_by)
            .where(explanation.c.detection_id == detection_id)
            .order_by(explanation.c.round)
        )
        .mappings()
        .all()
    )
    attachments = _attachments_by_explanation(conn, [r["id"] for r in rows])
    return [
        {
            "round": r["round"],
            "requested_by": r["requested_by_login"],  # None = 시스템 자동 요청
            "requested_at": r["requested_at"],
            "request_message": r["request_message"],
            "submitted_by": r["submitted_by_login"],
            "submitted_at": r["submitted_at"],
            "content": r["content"],
            "reviewed_by": r["reviewed_by_login"],
            "reviewed_at": r["reviewed_at"],
            "review_result": r["review_result"],
            "review_comment": r["review_comment"],
            # 담당자에게는 제출된 차수의 첨부만 — 취급자가 고치는 중인 초안은 숨긴다
            "attachments": (
                attachments.get(r["id"], [])
                if user.role == HANDLER or r["submitted_at"] is not None
                else []
            ),
        }
        for r in rows
    ]


def _history(conn: Connection, detection_id: int) -> list[dict]:
    actor = argus_user.alias("actor")
    rows = conn.execute(
        select(detection_status_history, actor.c.login_id.label("actor_login"))
        .outerjoin(actor, actor.c.id == detection_status_history.c.actor_user_id)
        .where(detection_status_history.c.detection_id == detection_id)
        .order_by(detection_status_history.c.id)
    ).mappings()
    return [
        {
            "from_status": r["from_status"],
            "to_status": r["to_status"],
            "round": r["round"],
            "actor": r["actor_login"],  # None = 시스템(탐지 배치)
            "comment": r["comment"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ── 상태 전이 ─────────────────────────────────────────────


class RequestBody(BaseModel):
    message: OptionalText = None


class DismissBody(BaseModel):
    reason: RequiredText


class SubmitBody(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class ReviewBody(BaseModel):
    comment: OptionalText = None


class RejectBody(BaseModel):
    comment: RequiredText


def _transition(
    request: Request, user: AuthenticatedUser, detection_id: int, action: str, text: str | None
) -> dict:
    with request.app.state.engine.begin() as conn:
        # 행을 잠그고 확인 — 탐지 배치가 같은 건에 기록을 보태는 순간과 겹치지 않게
        row = (
            conn.execute(
                _visible(select(detection.c.id, detection.c.status, detection.c.round), user)
                .where(detection.c.id == detection_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _not_found()
        if ACTION_ROLES[action] != user.role:
            raise ApiError(403, "FORBIDDEN", f"{action} is not allowed for {user.role}")
        transition = TRANSITIONS.get((action, row["status"]))
        if transition is None:
            raise ApiError(409, "INVALID_TRANSITION", f"cannot {action} a {row['status']} case")

        new_round = row["round"] + transition.round_step
        values: dict = {"status": transition.to_status, "round": new_round}
        if transition.closes:
            values["closed_at"] = func.now()
        if action == "dismiss":
            values["close_reason"] = text
        conn.execute(update(detection).where(detection.c.id == detection_id).values(**values))
        _record_explanation(conn, user, detection_id, action, row["round"], new_round, text)
        conn.execute(
            insert(detection_status_history).values(
                detection_id=detection_id,
                from_status=row["status"],
                to_status=transition.to_status,
                round=new_round,
                actor_user_id=user.id,
                comment=text,
            )
        )
    return {"id": detection_id, "status": transition.to_status, "round": new_round}


def _record_explanation(conn, user, detection_id, action, round_, new_round, text) -> None:
    """소명은 차수별 한 줄 — 요청 시 생성, 제출 시 채움, 검토 시 채움 (db-schema 3-4)"""
    current = (explanation.c.detection_id == detection_id) & (explanation.c.round == round_)
    if action == "request":
        conn.execute(
            insert(explanation).values(
                detection_id=detection_id,
                round=new_round,
                requested_by=user.id,
                request_message=text,
            )
        )
    elif action == "submit":
        conn.execute(
            update(explanation)
            .where(current)
            .values(submitted_by=user.id, submitted_at=func.now(), content=text)
        )
    elif action in ("approve", "reject"):
        conn.execute(
            update(explanation)
            .where(current)
            .values(
                reviewed_by=user.id,
                reviewed_at=func.now(),
                review_result="APPROVED" if action == "approve" else "REJECTED",
                review_comment=text,
            )
        )


@router.post("/{detection_id}/request")
@access_log_exempt(_EXEMPT_TRANSITION)
def request_explanation(
    detection_id: int, body: RequestBody, request: Request, user: CurrentUser
) -> dict:
    return _transition(request, user, detection_id, "request", body.message)


@router.post("/{detection_id}/dismiss")
@access_log_exempt(_EXEMPT_TRANSITION)
def dismiss(detection_id: int, body: DismissBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "dismiss", body.reason)


@router.post("/{detection_id}/submit")
@access_log_exempt(_EXEMPT_TRANSITION)
def submit(detection_id: int, body: SubmitBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "submit", body.content)


@router.post("/{detection_id}/approve")
@access_log_exempt(_EXEMPT_TRANSITION)
def approve(detection_id: int, body: ReviewBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "approve", body.comment)


@router.post("/{detection_id}/reject")
@access_log_exempt(_EXEMPT_TRANSITION)
def reject(detection_id: int, body: RejectBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "reject", body.comment)


@router.post("/{detection_id}/escalate")
@access_log_exempt(_EXEMPT_TRANSITION)
def escalate(detection_id: int, body: ReviewBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "escalate", body.comment)
