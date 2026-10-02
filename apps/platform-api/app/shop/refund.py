"""환불계좌 등록·변경·삭제 — 고객 마이페이지 (기능 레이어 7 결정 7)

계좌번호는 AES-256-GCM으로 암호화해 저장한다(§7②6호, app/crypto.py). 고객 본인 화면에도 끝 4자리만
보여 준다 — 본인 확인 수단(재인증)이 비밀번호뿐이라, 세션을 탈취당했을 때 전체 번호가 노출되지 않게.
"""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.commerce import BANKS
from app.crypto import refund_account_context
from app.models import refund_account
from app.shop.deps import CurrentMember
from app.shop.validation import Name

router = APIRouter(prefix="/shop/me/refund-account")

_ACCOUNT = re.compile(r"^\d{10,16}$")


def _account_number(value: str) -> str:
    digits = re.sub(r"[\s-]", "", value)
    if not _ACCOUNT.match(digits):
        raise ValueError("invalid account number")
    return digits


class RefundAccountRequest(BaseModel):
    bank_name: Literal[BANKS]  # type: ignore[valid-type]
    account_holder: Name
    account_number: Annotated[str, Field(max_length=30), AfterValidator(_account_number)]


def masked_view(row) -> dict | None:
    if row is None:
        return None
    return {
        "bank_name": row["bank_name"],
        "account_holder": row["account_holder"],
        "account_last4": row["account_last4"],
        "updated_at": row["updated_at"],
    }


def _load(conn, member_id: int):
    return (
        conn.execute(select(refund_account).where(refund_account.c.member_id == member_id))
        .mappings()
        .first()
    )


@router.get("")
def get_refund_account(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        return {"refund_account": masked_view(_load(conn, me.id)), "banks": list(BANKS)}


@router.put("")
def put_refund_account(body: RefundAccountRequest, request: Request, me: CurrentMember) -> dict:
    cipher = request.app.state.cipher
    encrypted = cipher.encrypt(body.account_number, refund_account_context(me.id))
    values = {
        "bank_name": body.bank_name,
        "account_holder": body.account_holder,
        "account_number_enc": encrypted,
        "account_last4": body.account_number[-4:],
    }
    with request.app.state.engine.begin() as conn:
        conn.execute(
            insert(refund_account)
            .values(member_id=me.id, **values)
            .on_conflict_do_update(
                index_elements=[refund_account.c.member_id],
                set_={**values, "updated_at": func.now()},
            )
        )
        return {"refund_account": masked_view(_load(conn, me.id)), "banks": list(BANKS)}


@router.delete("", status_code=204)
def delete_refund_account(request: Request, me: CurrentMember) -> None:
    with request.app.state.engine.begin() as conn:
        conn.execute(delete(refund_account).where(refund_account.c.member_id == me.id))
