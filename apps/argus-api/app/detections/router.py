"""탐지건 조회·소명 흐름 API (LOG-05~08, actor-flows F-05·F-06)

조회
- POST /api/detections/search         목록 — 조건(기간·룰·취급자·심각도·상태·경로)·정렬은 본문
    담당자: 전체 / 취급자: 본인 건 중 소명 요청을 받은 건(round ≥ 1) (v0.1 보강 C-1로 GET에서 바꿈)
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
- **본인 건은 처리할 수 없다**(v0.1 보강 H — 고시 §8② 점검의 객관성): 담당자가 탐지건의 행위자
  본인이면 모든 상태 전이가 403 `SELF_REVIEW_FORBIDDEN`. 열람은 된다. 거부된 시도는 상태 이력이
  아니라 Argus 자체 접속기록에 FAILURE로 남는다
- 취급자에게 보이지 않는 건(남의 건, 아직 요청 전인 건)은 403이 아니라 **404**
  — 존재 여부가 드러나지 않게
- 취급자 노출 범위를 "요청받은 건"으로 둔 이유:
  자동 요청이 가지 않은 DETECTED 건은 담당자의 검토 대기함이다
  (2026-10-01 결정, db-schema 3-1 "A5는 자신의 탐지건만 — 애플리케이션 계층에서 강제")

접속기록(LOG-17)
- 조회는 READ(ACCESS_LOG)로 기록하되 **건수만** — 화면에 보여준(마스킹된) 식별값의 수 (policy 6-3)
- 상태 전이는 접속기록이 아니라 **상태 이력(detection_status_history)**에 누가·언제·사유를 남긴다
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, model_validator
from sqlalchemy import Connection, and_, case, false, func, insert, select, update

from app.agent import (
    access_log,
    access_log_exempt,
    record_query_keys,
    record_refused_change,
    record_subject_count,
)
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detection.rules import KST
from app.detections.db_detail import db_detail
from app.detections.masking import mask_subject
from app.detections.transitions import ACTION_ROLES, HANDLER, OFFICER, TRANSITIONS
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
    source_system,
)
from app.notifications.events import due_at, on_requested, on_submitted
from app.notifications.webpush import dispatch_pending

router = APIRouter(prefix="/api/detections")

STATUSES = ("DETECTED", "REQUESTED", "SUBMITTED", "APPROVED", "REJECTED", "DISMISSED", "ESCALATED")
_EXEMPT_TRANSITION = "상태 변경은 detection_status_history에 누가·언제·사유를 기록 (api-spec 2-7)"

RequiredText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None

LoginId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[\x21-\x7e]{1,64}$")]

MAX_TICKETS = 3
# 플랫폼 1:1 문의 티켓 번호 — INQ-{문의 번호}. 링크 주소에 그대로 들어가므로 형식을 엄격히
TicketId = Annotated[
    str,
    # 공백·대소문자는 정리해 준 뒤 형식 검사 (inq-7 → INQ-7)
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^INQ-[1-9][0-9]{0,9}$"),
]


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


def _own_case(user: AuthenticatedUser):
    """담당자 본인이 행위자인 건인가 (v0.1 보강 H) — SQL 조건식
    - 담당자 계정에 연결된 명부가 있으면 그 (출처, 계정)이 탐지건 행위자와 같은가
    - 연결이 없으면 담당자 로그인 아이디 = 탐지건 행위자 아이디(출처 PLATFORM)"""
    if user.role != OFFICER:
        return false()
    if user.handler_id is not None:
        me = handler.alias("me")
        return (
            select(me.c.id)
            .where(
                me.c.id == user.handler_id,
                me.c.source_system_id == detection.c.source_system_id,
                me.c.login_id == detection.c.actor_login_id,
            )
            .exists()
        )
    platform = select(source_system.c.id).where(source_system.c.code == "PLATFORM")
    return and_(
        detection.c.source_system_id == platform.scalar_subquery(),
        detection.c.actor_login_id == user.login_id,
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
        "access_path": row[
            "access_path"
        ],  # APP = 화면 경유(3티어) / DB = DB 직접(2티어), policy 1-5
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
        # 소명 기한 — 제출을 기다리는 건(REQUESTED)만. 넘겨도 상태는 그대로 (v0.1 보강 F-3)
        "due_at": row["due_at"] if row["status"] == "REQUESTED" else None,
        # EVENT 탐지건의 처리 성격 — 데이터 유형·행위 구분 (v0.1 보강 J-2, AGGREGATE·이전 건은 null)
        "data_category": row["data_category"],
        "action_group": row["action_group"],
        # 현재 차수 소명 제출 뒤에 붙은 하위 기록 수 — 그 소명이 다루지 않은 행위 (J-1)
        "after_submission_count": row["after_submission_count"],
    }


# 현재 차수 소명의 기한
_current_due = select(explanation.c.due_at).where(
    explanation.c.detection_id == detection.c.id, explanation.c.round == detection.c.round
)
# 현재 차수 소명 제출 뒤에 붙은 하위 기록 수 — 아직 제출 전이면 제출 시각이 NULL이라 0건
_after_submission = (
    select(func.count())
    .select_from(
        detection_log.join(
            explanation,
            and_(
                explanation.c.detection_id == detection_log.c.detection_id,
                explanation.c.round == detection.c.round,
            ),
        )
    )
    .where(
        detection_log.c.detection_id == detection.c.id,
        detection_log.c.attached_at > explanation.c.submitted_at,
    )
    .correlate(detection)
)


def _case_query():
    return select(
        detection,
        _actor_name.scalar_subquery().label("actor_name"),
        _current_due.scalar_subquery().label("due_at"),
        _after_submission.scalar_subquery().label("after_submission_count"),
    )


# ── 조회 ──────────────────────────────────────────────────


class CaseSearch(BaseModel):
    """탐지건 검색 조건 (v0.1 보강 C-1) — URL이 아니라 본문으로 받는다(취급자 아이디가 서버
    접근 로그·방문 기록에 남지 않게, 접속기록 검색과 같은 방식).
    기간은 탐지 시각의 한국 날짜, 양 끝 포함. 비우면 기간 제한 없음(화면 기본값은 이번 달)"""

    status: Literal[STATUSES] | None = None
    access_path: Literal["APP", "DB"] | None = None
    severity: Literal["HIGH", "MEDIUM", "LOW"] | None = None
    rule_id: Annotated[int, Field(ge=1)] | None = None
    actor: LoginId | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort: Literal["LATEST", "SEVERITY"] = "LATEST"
    page: Annotated[int, Field(ge=1)] = 1
    size: Annotated[int, Field(ge=1, le=100)] = 20

    @model_validator(mode="after")
    def _period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


_SEVERITY_RANK = case(
    (detection.c.severity == "HIGH", 0), (detection.c.severity == "MEDIUM", 1), else_=2
)


@router.post("/search")
@access_log(action="READ", data_category="ACCESS_LOG")
def search_detections(body: CaseSearch, request: Request, user: CurrentUser) -> dict:
    """목록 — 담당자: 전체 / 취급자: 본인 건 중 소명 요청을 받은 건(조건보다 노출 범위가 먼저)"""
    # 검색어 값이 아니라 어떤 조건으로 찾았는지만 (policy 6-2)
    record_query_keys(sorted(body.model_dump(exclude_unset=True, exclude_none=True)))
    d = detection.c
    query = _visible(_case_query(), user)
    for column, value in (
        (d.status, body.status),
        (d.access_path, body.access_path),
        (d.severity, body.severity),
        (d.rule_id, body.rule_id),
        (d.actor_login_id, body.actor),
    ):
        if value is not None:
            query = query.where(column == value)
    if body.date_from:
        query = query.where(d.detected_at >= datetime.combine(body.date_from, time(), KST))
    if body.date_to:
        end = datetime.combine(body.date_to + timedelta(days=1), time(), KST)
        query = query.where(d.detected_at < end)
    order = (d.detected_at.desc(), d.id.desc())
    if body.sort == "SEVERITY":
        order = (_SEVERITY_RANK, *order)

    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = conn.execute(
            query.order_by(*order).limit(body.size).offset((body.page - 1) * body.size)
        ).mappings()
        items = [_case_summary(r) for r in rows]
    record_subject_count(0)  # 목록에는 정보주체 식별값이 없다 (건수 합계만)
    return {"items": items, "page": body.page, "size": body.size, "total": total}


@router.get("/{detection_id}")
@access_log(action="READ", data_category="ACCESS_LOG")
def get_detection(detection_id: int, request: Request, user: CurrentUser) -> dict:
    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(
                _visible(_case_query().add_columns(_own_case(user).label("own_case")), user).where(
                    detection.c.id == detection_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _not_found()
        logs = _logs(conn, detection_id)
        explanations = _explanations(conn, detection_id, user, request.app.state.platform_admin_url)
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
        # 담당자 본인이 행위자인 건 — 열람만, 처리는 다른 담당자가 (v0.1 보강 H)
        "own_case": bool(row["own_case"]),
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
            a.c.access_path,
            a.c.context,
            detection_log.c.attached_at,
            # 이 기록이 붙기 전에 제출된 가장 최근 차수 — 그 차수 소명 뒤에 붙은 기록 (J-1)
            select(func.max(explanation.c.round))
            .where(
                explanation.c.detection_id == detection_id,
                explanation.c.submitted_at < detection_log.c.attached_at,
            )
            .scalar_subquery()
            .label("after_round"),
            # 그 뒤 차수에서 다시 제출했다면 그 소명이 이 기록까지 다룬다
            select(explanation.c.id)
            .where(
                explanation.c.detection_id == detection_id,
                explanation.c.submitted_at >= detection_log.c.attached_at,
            )
            .exists()
            .label("covered"),
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
            # DB 직접(2티어) 기록이면 정규화 SQL·테이블·건수 — 무엇을 소명할지 (policy 1-5)
            "db": db_detail(r["access_path"], r["context"]),
            "attached_at": r["attached_at"],
            # N차 제출 뒤에 붙었고 그 뒤 제출이 없으면 N — 어떤 소명도 다루지 않은 행위
            "after_submission_round": None if r["covered"] else r["after_round"],
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


def _explanations(
    conn: Connection, detection_id: int, user: AuthenticatedUser, platform_url: str
) -> list[dict]:
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
            "due_at": r["due_at"],  # 소명 기한 (v0.1 보강 F-3)
            "submitted_by": r["submitted_by_login"],
            "submitted_at": r["submitted_at"],
            "content": r["content"],
            "reviewed_by": r["reviewed_by_login"],
            "reviewed_at": r["reviewed_at"],
            "review_result": r["review_result"],
            "review_comment": r["review_comment"],
            # 관련 업무 티켓 — 내용은 Argus에 없고, 링크로 플랫폼 관리자 화면에서 확인한다
            "tickets": [
                {"ticket_id": t, "url": f"{platform_url}/inquiries/{t.removeprefix('INQ-')}"}
                for t in r["ticket_ids"] or []
            ],
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
    # 관련 업무 티켓(1:1 문의) — 소명 내용과 별도 칸, 최대 3개. 형식만 검사하고 내용 검증은
    # 취급자·담당자가 한다(Argus는 티켓 내용을 갖지 않음 — 절대 규칙 #3)
    ticket_ids: Annotated[list[TicketId], Field(max_length=MAX_TICKETS)] = []


class ReviewBody(BaseModel):
    comment: OptionalText = None


class RejectBody(BaseModel):
    comment: RequiredText


def _transition(
    request: Request,
    user: AuthenticatedUser,
    detection_id: int,
    action: str,
    text: str | None,
    ticket_ids: list[str] | None = None,
) -> dict:
    with request.app.state.engine.begin() as conn:
        # 행을 잠그고 확인 — 탐지 배치가 같은 건에 기록을 보태는 순간과 겹치지 않게
        row = (
            conn.execute(
                _visible(
                    select(
                        detection.c.id,
                        detection.c.status,
                        detection.c.round,
                        detection.c.severity,
                        _own_case(user).label("own_case"),
                    ),
                    user,
                )
                .where(detection.c.id == detection_id)
                .with_for_update(of=detection)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _not_found()
        if ACTION_ROLES[action] != user.role:
            raise ApiError(403, "FORBIDDEN", f"{action} is not allowed for {user.role}")
        if row["own_case"]:
            # 본인 건 — 어떤 상태든 거부. 상태 이력에는 남지 않으므로 자체 접속기록에 FAILURE로
            record_refused_change(detection_id)
            raise ApiError(
                403, "SELF_REVIEW_FORBIDDEN", "your own case must be handled by another officer"
            )
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
        _record_explanation(
            conn, user, detection_id, action, row["round"], new_round, text, ticket_ids
        )
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
        # 화면 알림 (v0.1 보강 F-1) — 상태 변경과 같은 트랜잭션
        if action == "request":
            on_requested(conn, detection_id, row["severity"], new_round)
        elif action == "submit":
            requested_by = conn.execute(
                select(explanation.c.requested_by).where(
                    explanation.c.detection_id == detection_id,
                    explanation.c.round == row["round"],
                )
            ).scalar_one_or_none()
            on_submitted(conn, detection_id, row["severity"], row["round"], requested_by)
    return {"id": detection_id, "status": transition.to_status, "round": new_round}


def _record_explanation(
    conn, user, detection_id, action, round_, new_round, text, ticket_ids=None
) -> None:
    """소명은 차수별 한 줄 — 요청 시 생성, 제출 시 채움, 검토 시 채움 (db-schema 3-4)"""
    current = (explanation.c.detection_id == detection_id) & (explanation.c.round == round_)
    if action == "request":
        conn.execute(
            insert(explanation).values(
                detection_id=detection_id,
                round=new_round,
                requested_by=user.id,
                request_message=text,
                due_at=due_at(conn),  # 소명 기한 — 재요청은 다시 기본 7일 (F-3)
            )
        )
    elif action == "submit":
        conn.execute(
            update(explanation)
            .where(current)
            .values(
                submitted_by=user.id,
                submitted_at=func.now(),
                content=text,
                # 같은 번호를 두 번 적어도 한 번만 (입력 순서 유지)
                ticket_ids=list(dict.fromkeys(ticket_ids or [])),
            )
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
    detection_id: int,
    body: RequestBody,
    request: Request,
    user: CurrentUser,
    background: BackgroundTasks,
) -> dict:
    result = _transition(request, user, detection_id, "request", body.message)
    # 상 심각도 소명 요청은 웹 푸시도 — 응답을 보낸 뒤 발송해 담당자 화면을 붙잡지 않는다 (F-4)
    background.add_task(dispatch_pending, request.app.state.engine, request.app.state.vapid)
    return result


@router.post("/{detection_id}/dismiss")
@access_log_exempt(_EXEMPT_TRANSITION)
def dismiss(detection_id: int, body: DismissBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "dismiss", body.reason)


@router.post("/{detection_id}/submit")
@access_log_exempt(_EXEMPT_TRANSITION)
def submit(detection_id: int, body: SubmitBody, request: Request, user: CurrentUser) -> dict:
    return _transition(request, user, detection_id, "submit", body.content, body.ticket_ids)


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
