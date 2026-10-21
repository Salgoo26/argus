"""접수 건 요청·응답 형식

쇼핑몰이 보내는 항목은 운송장에 찍히는 수준이다 — 받는 사람 이름·연락처·주소, 배송 메모,
상품명·수량, 주문번호, 보내는 곳(쇼핑몰). 회원번호·이메일은 받지 않는다(커머스 확장 판단기록 F6 Q2).
"""

from typing import Annotated

from pydantic import BaseModel, Field

from app.db import STATUS_LABELS

ORDER_NO = r"^[A-Za-z0-9_-]{1,64}$"


class Party(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=50)]
    phone: Annotated[str, Field(min_length=1, max_length=20)]
    zipCode: Annotated[str, Field(min_length=1, max_length=10)]  # noqa: N815 — API 필드 이름
    address: Annotated[str, Field(min_length=1, max_length=255)]
    addressDetail: Annotated[str, Field(max_length=100)] | None = None  # noqa: N815


class Item(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    quantity: Annotated[int, Field(ge=1, le=99)]


class CreateShipment(BaseModel):
    orderNo: Annotated[str, Field(pattern=ORDER_NO)]  # noqa: N815
    sender: Party
    receiver: Party
    memo: Annotated[str, Field(max_length=100)] | None = None
    items: Annotated[list[Item], Field(min_length=1, max_length=20)]


class CreateReturn(BaseModel):
    orderNo: Annotated[str, Field(pattern=ORDER_NO)]  # noqa: N815
    pickup: Party  # 회수할 곳 (고객)
    returnTo: Party  # noqa: N815 — 돌려보낼 곳 (쇼핑몰)
    originalTrackingNo: Annotated[str, Field(max_length=20)] | None = None  # noqa: N815
    memo: Annotated[str, Field(max_length=100)] | None = None
    items: Annotated[list[Item], Field(min_length=1, max_length=20)]


def _iso(value):
    return value.isoformat() if value else None


def shipment_json(row, company: str, events: list | None = None) -> dict:
    """상태 조회·통지 공통 — 받는 사람 정보는 되돌려주지 않는다(쇼핑몰이 보낸 값)"""
    body = {
        "shipmentId": row["shipment_id"],
        "kind": row["kind"],
        "orderNo": row["client_order_no"],
        "courier": company,
        "status": row["status"],
        "statusLabel": STATUS_LABELS.get(row["status"], row["status"]),
        "trackingNo": row["tracking_no"],
        "createdAt": _iso(row["created_at"]),
        "statusChangedAt": _iso(row["status_changed_at"]),
        "pickedUpAt": _iso(row["picked_up_at"]),
        "deliveredAt": _iso(row["delivered_at"]),
        "canceledAt": _iso(row["canceled_at"]),
    }
    if events is not None:
        body["events"] = [
            {
                "status": e["status"],
                "statusLabel": STATUS_LABELS.get(e["status"], e["status"]),
                "occurredAt": _iso(e["occurred_at"]),
            }
            for e in events
        ]
    return body
