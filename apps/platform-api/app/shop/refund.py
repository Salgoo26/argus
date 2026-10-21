"""환불계좌 관리 — 고객 마이페이지 (기능 레이어 7 결정 7, 커머스 확장 F7-1)

배송지처럼 여러 개를 등록해 두고, 무통장입금 주문을 취소·반품할 때 이 목록에서 하나를 고른다
(고른 계좌는 취소·반품 기록에 사본으로 남아, 나중에 여기서 지우거나 고쳐도 환불 처리는 그대로).
- 회원당 최대 MAX_ACCOUNTS개 — 한 계정이 행을 무한정 만들지 못하게
- 계좌번호는 AES-256-GCM으로 암호화해 저장한다(§7②6호, app/crypto.py). 고객 본인 화면에도
  끝 4자리만 보여 준다 — 본인 확인 수단(재인증)이 비밀번호뿐이라, 세션을 탈취당했을 때
  전체 번호가 노출되지 않게
- 남의 계좌 번호로는 아무것도 할 수 없다 — 모든 조회·변경에 member_id 조건(없으면 404)

고객 본인 행위라 접속기록 대상이 아니다 (CLAUDE.md 3절 #4).
"""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import Connection, delete, func, insert, select, update

from app.commerce import BANKS
from app.crypto import refund_account_context
from app.errors import ApiError
from app.models import member, refund_account
from app.shop.deps import CurrentMember
from app.shop.validation import Name

router = APIRouter(prefix="/shop/me/refund-accounts")

MAX_ACCOUNTS = 5
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


def masked_view(row) -> dict:
    return {
        "id": row["id"],
        "bank_name": row["bank_name"],
        "account_holder": row["account_holder"],
        "account_last4": row["account_last4"],
        "updated_at": row["updated_at"],
    }


def account_rows(conn: Connection, member_id: int) -> list[dict]:
    rows = conn.execute(
        select(refund_account)
        .where(refund_account.c.member_id == member_id)
        .order_by(refund_account.c.id)
    ).mappings()
    return [masked_view(r) for r in rows]


def owned_account(conn: Connection, member_id: int, account_id: int):
    row = (
        conn.execute(
            select(refund_account).where(
                refund_account.c.id == account_id, refund_account.c.member_id == member_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "refund account not found")
    return row


def _listing(conn: Connection, member_id: int) -> dict:
    return {"items": account_rows(conn, member_id), "banks": list(BANKS), "max": MAX_ACCOUNTS}


def _values(request: Request, member_id: int, body: RefundAccountRequest) -> dict:
    encrypted = request.app.state.cipher.encrypt(
        body.account_number, refund_account_context(member_id)
    )
    return {
        "bank_name": body.bank_name,
        "account_holder": body.account_holder,
        "account_number_enc": encrypted,
        "account_last4": body.account_number[-4:],
    }


@router.get("")
def list_refund_accounts(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        return _listing(conn, me.id)


@router.post("", status_code=201)
def add_refund_account(body: RefundAccountRequest, request: Request, me: CurrentMember) -> dict:
    values = _values(request, me.id, body)
    with request.app.state.engine.begin() as conn:
        # 회원 행을 잠가 개수 상한을 동시 요청에서도 지킨다
        conn.execute(select(member.c.id).where(member.c.id == me.id).with_for_update())
        count = conn.execute(
            select(func.count())
            .select_from(refund_account)
            .where(refund_account.c.member_id == me.id)
        ).scalar_one()
        if count >= MAX_ACCOUNTS:
            raise ApiError(409, "TOO_MANY_REFUND_ACCOUNTS", f"up to {MAX_ACCOUNTS} accounts")
        conn.execute(insert(refund_account).values(member_id=me.id, **values))
        return _listing(conn, me.id)


@router.put("/{account_id}")
def update_refund_account(
    account_id: int, body: RefundAccountRequest, request: Request, me: CurrentMember
) -> dict:
    values = _values(request, me.id, body)
    with request.app.state.engine.begin() as conn:
        owned_account(conn, me.id, account_id)
        conn.execute(
            update(refund_account)
            .where(refund_account.c.id == account_id)
            .values(**values, updated_at=func.now())
        )
        return _listing(conn, me.id)


@router.delete("/{account_id}")
def delete_refund_account(account_id: int, request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.begin() as conn:
        owned_account(conn, me.id, account_id)
        conn.execute(delete(refund_account).where(refund_account.c.id == account_id))
        return _listing(conn, me.id)
