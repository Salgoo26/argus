"""1:1 문의 처리 — CS (PLT-17, actor-flows F-02) — 감시 대상

F-02 흐름: 문의 목록(READ) → 문의 상세(READ, **티켓 ID 확보**) → 회원 상세(READ) → 답변(UPDATE).
문의 화면의 접속기록에는 context.ticket_id = "INQ-{문의번호}"를 싣는다(api-spec 2-4). 그래야
"CS가 이 고객을 왜 조회했나"를 Argus에서 문의 번호로 설명할 수 있다(소명 근거 — 결정 9).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import func, select, true, update

from app.agent import access_log, record_context, record_subjects
from app.auth.deps import CurrentOperator
from app.errors import ApiError
from app.models import inquiry, member, operator
from app.shop.inquiries import _not_blank

router = APIRouter(prefix="/admin/inquiries")


def ticket_id(inquiry_id: int) -> str:
    return f"INQ-{inquiry_id}"


class AnswerRequest(BaseModel):
    answer: Annotated[str, Field(min_length=1, max_length=5000), AfterValidator(_not_blank)]


@router.get("")
@access_log(action="READ", data_category="INQUIRY")
def list_inquiries(
    request: Request,
    _operator: CurrentOperator,
    status: Literal["OPEN", "ANSWERED"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """목록에는 본문을 싣지 않는다 — 제목·작성자·상태만 (최소 노출)"""
    condition = inquiry.c.status == status if status else true()
    with request.app.state.engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(inquiry).where(condition)
        ).scalar_one()
        rows = conn.execute(
            select(
                inquiry.c.id,
                inquiry.c.member_id,
                member.c.name.label("member_name"),
                inquiry.c.title,
                inquiry.c.status,
                inquiry.c.created_at,
                inquiry.c.answered_at,
            )
            .select_from(inquiry.outerjoin(member, member.c.id == inquiry.c.member_id))
            .where(condition)
            .order_by(inquiry.c.created_at.desc(), inquiry.c.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [dict(r) for r in rows]
    # 처리한 정보주체 = 화면에 보인 문의의 작성자 (actor-flows F-02 #2 "문의 작성자 목록")
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": page, "size": size, "total": total}


def _load(conn, inquiry_id: int):
    answered_by = operator.alias("answered_by_operator")
    return (
        conn.execute(
            select(
                inquiry,
                member.c.name.label("member_name"),
                member.c.email.label("member_email"),
                answered_by.c.login_id.label("answered_by_login_id"),
            )
            .select_from(
                inquiry.outerjoin(member, member.c.id == inquiry.c.member_id).outerjoin(
                    answered_by, answered_by.c.id == inquiry.c.answered_by
                )
            )
            .where(inquiry.c.id == inquiry_id)
        )
        .mappings()
        .first()
    )


def _detail(row) -> dict:
    result = dict(row)
    result.pop("answered_by", None)  # 내부 PK 대신 계정(login_id)으로
    result["ticket_id"] = ticket_id(row["id"])
    return result


@router.get("/{inquiry_id}")
@access_log(action="READ", data_category="INQUIRY")
def get_inquiry(inquiry_id: int, request: Request, _operator: CurrentOperator) -> dict:
    record_context(ticket_id=ticket_id(inquiry_id))
    with request.app.state.engine.connect() as conn:
        row = _load(conn, inquiry_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "inquiry not found")
    record_subjects([row["member_id"]] if row["member_id"] is not None else [])
    return _detail(row)


@router.post("/{inquiry_id}/answer")
@access_log(action="UPDATE", data_category="INQUIRY")
def answer_inquiry(
    inquiry_id: int, body: AnswerRequest, request: Request, operator_: CurrentOperator
) -> dict:
    """답변 등록 — 한 번만(OPEN → ANSWERED). 고친 답변은 범위 밖(v0.2 후보)"""
    record_context(ticket_id=ticket_id(inquiry_id))
    with request.app.state.engine.begin() as conn:
        current = conn.execute(
            select(inquiry.c.member_id, inquiry.c.status)
            .where(inquiry.c.id == inquiry_id)
            .with_for_update()
        ).first()
        if current is None:
            error = ApiError(404, "NOT_FOUND", "inquiry not found")
        else:
            # 업무가 실패해도(이미 답변됨) 누구의 문의를 처리하려 했는지 남긴다
            record_subjects([current.member_id] if current.member_id is not None else [])
            if current.status != "OPEN":
                error = ApiError(409, "ALREADY_ANSWERED", "inquiry already answered")
            else:
                conn.execute(
                    update(inquiry)
                    .where(inquiry.c.id == inquiry_id)
                    .values(
                        status="ANSWERED",
                        answer=body.answer,
                        answered_by=operator_.id,
                        answered_at=func.now(),
                    )
                )
                error = None
        row = _load(conn, inquiry_id) if error is None else None
    if error is not None:
        raise error
    return _detail(row)
