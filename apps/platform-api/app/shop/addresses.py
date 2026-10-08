"""배송지 관리 — 마이페이지 (기능 레이어 7-4 ②, PLT-03)

회원당 여러 개, 기본 배송지 1개. 주문할 때 이 중 하나를 고르면 주문에 그 시점 값이 복사된다(PR ③).
- 회원당 최대 MAX_ADDRESSES개 — 한 계정이 행을 무한정 만들지 못하게
- 첫 배송지는 자동으로 기본. 기본 배송지를 지우면 남은 것 중 가장 먼저 등록한 것이 기본이 된다
- 남의 배송지 번호로는 아무것도 할 수 없다 — 모든 조회·변경에 member_id 조건(없으면 404)
- 쓰기는 회원 행을 잠그고 한다: 개수 상한·기본 배송지 1개를 동시 요청에서도 지키기 위해
  (기본 배송지 1개는 DB 부분 유니크 인덱스로도 막혀 있다)

고객 본인 행위라 접속기록 대상이 아니다 (CLAUDE.md 3절 #4).
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import Connection, delete, func, insert, select, update

from app.errors import ApiError
from app.models import member, shipping_address
from app.shop.deps import CurrentMember
from app.shop.validation import AddressDetail, AddressLine, Label, Name, Phone, ZipCode

router = APIRouter(prefix="/shop/me/addresses")

MAX_ADDRESSES = 10
_FIELDS = ("label", "recipient", "phone", "zip_code", "address", "address_detail")


class AddressIn(BaseModel):
    label: Label
    recipient: Name
    phone: Phone
    zip_code: ZipCode
    address: AddressLine
    address_detail: AddressDetail = None
    is_default: bool = False


def address_rows(conn: Connection, member_id: int) -> list[dict]:
    rows = conn.execute(
        select(shipping_address.c.id, *(shipping_address.c[f] for f in _FIELDS))
        .add_columns(shipping_address.c.is_default)
        .where(shipping_address.c.member_id == member_id)
        .order_by(shipping_address.c.is_default.desc(), shipping_address.c.id)
    ).mappings()
    return [dict(r) for r in rows]


def _lock_member(conn: Connection, member_id: int) -> None:
    conn.execute(select(member.c.id).where(member.c.id == member_id).with_for_update())


def _owned(conn: Connection, member_id: int, address_id: int) -> dict:
    row = (
        conn.execute(
            select(shipping_address).where(
                shipping_address.c.id == address_id, shipping_address.c.member_id == member_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "address not found")
    return dict(row)


def _make_default(conn: Connection, member_id: int, address_id: int) -> None:
    # 기존 기본을 먼저 내리고 올린다 — 부분 유니크 인덱스(회원당 기본 1개)에 걸리지 않게
    conn.execute(
        update(shipping_address)
        .where(shipping_address.c.member_id == member_id, shipping_address.c.is_default)
        .values(is_default=False, updated_at=func.now())
    )
    conn.execute(
        update(shipping_address)
        .where(shipping_address.c.id == address_id)
        .values(is_default=True, updated_at=func.now())
    )


@router.get("")
def list_addresses(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        return {"items": address_rows(conn, me.id), "max": MAX_ADDRESSES}


@router.post("", status_code=201)
def add_address(body: AddressIn, request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.begin() as conn:
        _lock_member(conn, me.id)
        count = conn.execute(
            select(func.count())
            .select_from(shipping_address)
            .where(shipping_address.c.member_id == me.id)
        ).scalar_one()
        if count >= MAX_ADDRESSES:
            raise ApiError(400, "ADDRESS_LIMIT", f"up to {MAX_ADDRESSES} addresses")
        address_id = conn.execute(
            insert(shipping_address)
            .values(member_id=me.id, is_default=False, **body.model_dump(include=set(_FIELDS)))
            .returning(shipping_address.c.id)
        ).scalar_one()
        if count == 0 or body.is_default:
            _make_default(conn, me.id, address_id)
        return {"items": address_rows(conn, me.id), "max": MAX_ADDRESSES}


@router.put("/{address_id}")
def update_address(address_id: int, body: AddressIn, request: Request, me: CurrentMember) -> dict:
    """내용 수정. 기본 배송지 해제는 없다 — 다른 배송지를 기본으로 지정하면 바뀐다"""
    with request.app.state.engine.begin() as conn:
        _lock_member(conn, me.id)
        _owned(conn, me.id, address_id)
        conn.execute(
            update(shipping_address)
            .where(shipping_address.c.id == address_id)
            .values(updated_at=func.now(), **body.model_dump(include=set(_FIELDS)))
        )
        if body.is_default:
            _make_default(conn, me.id, address_id)
        return {"items": address_rows(conn, me.id), "max": MAX_ADDRESSES}


@router.post("/{address_id}/default")
def set_default(address_id: int, request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.begin() as conn:
        _lock_member(conn, me.id)
        _owned(conn, me.id, address_id)
        _make_default(conn, me.id, address_id)
        return {"items": address_rows(conn, me.id), "max": MAX_ADDRESSES}


@router.delete("/{address_id}")
def delete_address(address_id: int, request: Request, me: CurrentMember) -> dict:
    """실제 삭제. 이미 주문에 쓴 배송지여도 주문에는 그때 값이 복사돼 있어 영향이 없다"""
    with request.app.state.engine.begin() as conn:
        _lock_member(conn, me.id)
        row = _owned(conn, me.id, address_id)
        conn.execute(delete(shipping_address).where(shipping_address.c.id == address_id))
        if row["is_default"]:
            oldest = conn.execute(
                select(func.min(shipping_address.c.id)).where(shipping_address.c.member_id == me.id)
            ).scalar_one()
            if oldest is not None:
                _make_default(conn, me.id, oldest)
        return {"items": address_rows(conn, me.id), "max": MAX_ADDRESSES}
