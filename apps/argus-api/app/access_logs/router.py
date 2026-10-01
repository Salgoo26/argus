"""접속기록 조회·검색 — 담당자 전용 (LOG-02, §8② 접속기록 점검, 기능 레이어 3)

탐지건 화면이 "룰에 걸린 건의 결재함"이라면, 이 API는 원장 전체를 조건으로 찾아보는 점검 도구다.
탐지되지 않은 기록도 볼 수 있어야 정기 점검(§8②)과 정보주체 열람 청구 대응이 가능하다.

- POST /api/access-logs/search   조건: 계정·기간(KST 날짜)·수행업무·정보주체·접근 경로·출처

검색 조건은 **URL이 아니라 요청 본문**으로 받는다 — `?subject=10293`처럼 URL에 넣으면 회원번호가
서버 접근 로그·브라우저 방문 기록에 남는다 (2026-10-01 결정).

접속기록(LOG-17, policy 6-2·6-3):
- READ(ACCESS_LOG)로 기록하되 정보주체는 **건수만**(화면에 보여 준 마스킹 값의 수)
- 검색 조건은 **키 이름만** — "정보주체로 검색했다"는 남기고 검색어 값은 남기지 않는다

출처 기본값은 PLATFORM. Argus 자체 기록(ARGUS)도 고를 수는 있지만 전용 점검 기능은 두지 않는다
(2026-10-01 사용자 결정 — 실무에서 점검 시스템 자체 접근 기록은 정기 검토 대상이 아닌 경우가 많고,
§8② 점검 근거로 "볼 수는 있다"만 남긴다).
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import Connection, Text, and_, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY

from app.agent import access_log, record_query_keys, record_subject_count
from app.auth.deps import CurrentUser
from app.detection.rules import KST
from app.detections.masking import mask_subject
from app.detections.transitions import OFFICER
from app.errors import ApiError
from app.models import access_log as access_log_table
from app.models import detection_log, handler, source_system

router = APIRouter(prefix="/api/access-logs")

ACTIONS = ("LOGIN", "READ", "CREATE", "UPDATE", "DELETE", "DOWNLOAD", "EXPORT", "UNMASK")
DEFAULT_DAYS = 7  # 기간을 비우면 오늘 포함 최근 7일
MAX_DAYS = 366  # 한 번에 1년 — 접속기록 보관기간(policy 5-1)
SUBJECT_PREVIEW = 5  # 한 기록에서 보여 주는 정보주체 수 — 나머지는 "외 N명" (최소 노출)
_MEMBER_PREFIX = "member_"  # 화면 표기 member_10293도 받는다 (db-schema 2-1)

LoginId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[\x21-\x7e]{1,64}$")]


class SearchBody(BaseModel):
    actor: LoginId | None = None
    date_from: date | None = None  # 한국 날짜, 양 끝 포함
    date_to: date | None = None
    action: Annotated[str, StringConstraints(pattern="^(" + "|".join(ACTIONS) + ")$")] | None = None
    subject: LoginId | None = None  # 회원번호 — "10293" 또는 "member_10293"
    access_path: Literal["APP", "DB"] | None = None
    source: Literal["PLATFORM", "ARGUS"] = "PLATFORM"
    page: Annotated[int, Field(ge=1)] = 1
    size: Annotated[int, Field(ge=1, le=100)] = 50

    @model_validator(mode="after")
    def _period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


def _period(body: SearchBody) -> tuple[datetime, datetime]:
    """[시작일 00:00, 종료일 다음날 00:00) — 한국 시각"""
    today = datetime.now(KST).date()
    if body.date_to:
        date_to = body.date_to
    elif body.date_from:
        date_to = body.date_from + timedelta(days=DEFAULT_DAYS - 1)
    else:
        date_to = today
    date_from = body.date_from or date_to - timedelta(days=DEFAULT_DAYS - 1)
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise ApiError(400, "PERIOD_TOO_LONG", f"period must be at most {MAX_DAYS} days")
    return (
        datetime.combine(date_from, time(), KST),
        datetime.combine(date_to + timedelta(days=1), time(), KST),
    )


def _filters(body: SearchBody, start: datetime, end: datetime) -> list:
    a = access_log_table
    conditions = [
        source_system.c.code == body.source,
        a.c.occurred_at >= start,
        a.c.occurred_at < end,
    ]
    if body.actor:
        conditions.append(a.c.actor_login_id == body.actor)
    if body.action:
        conditions.append(a.c.action == body.action)
    if body.access_path:
        conditions.append(a.c.access_path == body.access_path)
    if body.subject:
        subject = body.subject.removeprefix(_MEMBER_PREFIX)
        # subject_ids @> {값} — GIN 인덱스(ix_access_log_subject) 사용.
        # 1,000명 초과로 잘린 기록은 앞 1,000개만 저장돼 있어 빠질 수 있다(화면에 안내)
        # DB 컬럼은 text[] — 같은 타입으로 맞춰야 연산자·인덱스가 맞는다
        conditions.append(a.c.subject_ids.contains(cast([subject], ARRAY(Text))))
    return conditions


_actor_name = (
    select(handler.c.name)
    .where(
        and_(
            handler.c.source_system_id == access_log_table.c.source_system_id,
            handler.c.login_id == access_log_table.c.actor_login_id,
        )
    )
    .scalar_subquery()
)


@router.post("/search")
@access_log(action="READ", data_category="ACCESS_LOG")
def search(body: SearchBody, request: Request, user: CurrentUser) -> dict:
    # 검색어 값이 아니라 "어떤 조건으로 찾았는지"만 — 본문에 실제로 넣은 조건의 이름 (policy 6-2)
    record_query_keys(sorted(body.model_dump(exclude_unset=True, exclude_none=True)))
    if user.role != OFFICER:
        # 원장 전체 검색은 점검 업무 — 취급자는 자기 소명 건만 본다 (actor-flows F-06)
        raise ApiError(403, "FORBIDDEN", "access log search is for officers only")

    start, end = _period(body)
    a = access_log_table
    base = (
        select(
            a.c.id,
            a.c.occurred_at,
            source_system.c.code.label("source"),
            a.c.actor_login_id,
            _actor_name.label("actor_name"),
            a.c.client_ip,
            a.c.access_path,
            a.c.action,
            a.c.data_category,
            a.c.result,
            a.c.request_method,
            a.c.request_path,
            a.c.subject_type,
            a.c.subject_ids,
            a.c.subject_count,
            a.c.subject_truncated,
        )
        .join(source_system, source_system.c.id == a.c.source_system_id)
        .where(*_filters(body, start, end))
    )
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        rows = (
            conn.execute(
                base.order_by(a.c.occurred_at.desc(), a.c.id.desc())
                .limit(body.size)
                .offset((body.page - 1) * body.size)
            )
            .mappings()
            .all()
        )
        detections = _detections_of(conn, [r["id"] for r in rows])

    items = [_item(r, detections.get(r["id"], [])) for r in rows]
    record_subject_count(sum(len(item["subjects"]) for item in items))
    return {
        "items": items,
        "page": body.page,
        "size": body.size,
        "total": total,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
    }


def _detections_of(conn: Connection, log_ids: list[int]) -> dict[int, list[int]]:
    """기록 → 그 기록을 근거로 가진 탐지건들 (탐지건 화면으로 가는 링크)"""
    if not log_ids:
        return {}
    rows = conn.execute(
        select(detection_log.c.access_log_id, detection_log.c.detection_id)
        .where(detection_log.c.access_log_id.in_(log_ids))
        .order_by(detection_log.c.detection_id)
    )
    linked: dict[int, list[int]] = {}
    for log_id, detection_id in rows:
        linked.setdefault(log_id, []).append(detection_id)
    return linked


def _item(row, detection_ids: list[int]) -> dict:
    ids = row["subject_ids"] or []
    return {
        "id": row["id"],
        "occurred_at": row["occurred_at"],
        "source": row["source"],
        "actor_login_id": row["actor_login_id"],
        "actor_name": row["actor_name"],
        "client_ip": str(row["client_ip"]),
        "access_path": row["access_path"],
        "action": row["action"],
        "data_category": row["data_category"],
        "result": row["result"],
        "request_method": row["request_method"],
        "request_path": row["request_path"],
        "subject_count": row["subject_count"],
        "subject_truncated": row["subject_truncated"],
        # 원본 식별값은 싣지 않는다 — 마스킹 값 앞 몇 개만 (LOG-10, 최소 노출)
        "subjects": [mask_subject(row["subject_type"], s) for s in ids[:SUBJECT_PREVIEW]],
        "detection_ids": detection_ids,
    }
