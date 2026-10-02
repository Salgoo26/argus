"""1:1 문의 — 고객 작성·내 문의 보기 (PLT-06)

이 문의가 CS가 해당 고객 정보를 조회하는 업무 근거가 된다(actor-flows F-01 #5, F-02).
고객 행위라 접속기록 대상이 아니다 (CLAUDE.md 3절 #4).
"""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import insert, select

from app.models import inquiry
from app.shop.deps import CurrentMember

router = APIRouter(prefix="/shop/inquiries")


def _not_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("blank")
    return value


class InquiryRequest(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_not_blank)]
    body: Annotated[str, Field(min_length=1, max_length=5000), AfterValidator(_not_blank)]


_COLUMNS = (
    inquiry.c.id,
    inquiry.c.title,
    inquiry.c.body,
    inquiry.c.status,
    inquiry.c.answer,
    inquiry.c.created_at,
    inquiry.c.answered_at,
)


@router.post("", status_code=201)
def create_inquiry(body: InquiryRequest, request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.begin() as conn:
        row = (
            conn.execute(
                insert(inquiry)
                .values(member_id=me.id, title=body.title, body=body.body)
                .returning(*_COLUMNS)
            )
            .mappings()
            .one()
        )
    return dict(row)


@router.get("")
def my_inquiries(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(
            select(*_COLUMNS)
            .where(inquiry.c.member_id == me.id)
            .order_by(inquiry.c.created_at.desc(), inquiry.c.id.desc())
        ).mappings()
        # 답변한 직원은 보여 주지 않는다 — 고객에게 필요 없는 직원 정보 (최소 노출)
        return {"items": [dict(r) for r in rows]}
