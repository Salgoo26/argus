"""주문·결제 조회 — 관리자 (PLT-16) — 감시 대상

목록에는 결제수단 정보가 카드사 이름뿐이다(카드번호는 저장하지 않음) → 데이터 유형 ORDER.
상세에는 배송 정보(주문 시점 스냅샷)가 더해진다 — 역시 주문 조회 ORDER (db-schema 4절 플랫폼 보강).
커머스 확장(0011): 상세에 배송 진행(가상 택배사·송장번호)·취소/반품 기록·상태 이력이 더해진다.
가상 PG·택배 연동(0013): 상세에 PG 승인 결과(마스킹 카드번호·승인번호·할부·영수증 주소)·배송 메모·
택배 접수 번호·택배사 상태·반품 회수 상태가 더해진다. 카드번호 전체는 플랫폼에 없다.
무통장입금(0014): 상세에 입금 계좌·입금자명·기한·입금 확인, 취소/반품의 환불 방식·이체 계좌
(끝 4자리)가 더해진다. 입금 확인·환불 완료 처리는 UPDATE·ORDER,
이체 계좌 전체 번호는 따로 — READ·결제수단(PAYMENT).
처리한 정보주체 = 화면에 표시된 주문의 회원(탈퇴로 끊긴 주문은 정보주체 없음).
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.admin_search import SearchPage, check_period, kst_period
from app.agent import access_log, record_subjects
from app.auth.deps import OrdersOperator, RefundViewOperator
from app.crypto import claim_refund_context
from app.errors import ApiError
from app.fulfillment import (
    STATUSES,
    claims_of,
    complete_refund,
    confirm_deposit,
    expected_delivery,
    history_of,
    lock_order,
    pending_refund_claim,
    withdrawal_deadline,
)
from app.models import SHIP_COLUMNS, bank_deposit, member, order_claim, orders, payment, product

router = APIRouter(prefix="/admin/orders")


def _list_query():
    """목록·검색 공통 — 결제수단 정보는 카드사 이름뿐"""
    return select(
        orders.c.id,
        orders.c.member_id,
        member.c.name.label("member_name"),
        product.c.name.label("product_name"),
        orders.c.amount,
        orders.c.status,
        orders.c.ordered_at,
        payment.c.method.label("payment_method"),
        payment.c.card_company,
        payment.c.pg_tid,
    ).select_from(
        orders.join(product, product.c.id == orders.c.product_id)
        .outerjoin(member, member.c.id == orders.c.member_id)
        .outerjoin(payment, payment.c.order_id == orders.c.id)
    )


@router.get("")
@access_log(action="READ", data_category="ORDER")
def list_orders(
    request: Request,
    _operator: OrdersOperator,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(orders)).scalar_one()
        rows = conn.execute(
            _list_query()
            .order_by(orders.c.ordered_at.desc(), orders.c.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [dict(r) for r in rows]
    # 같은 회원의 주문이 여럿이어도 정보주체는 한 번만
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": page, "size": size, "total": total}


class OrderSearch(SearchPage):
    order_id: Annotated[int, Field(ge=1)] | None = None
    member_id: Annotated[int, Field(ge=1)] | None = None
    status: Literal[STATUSES] | None = None  # type: ignore[valid-type]  # 0011·0013 상태
    ordered_from: date | None = None  # 한국 날짜, 양 끝 포함
    ordered_to: date | None = None

    @model_validator(mode="after")
    def _period(self):
        check_period(self.ordered_from, self.ordered_to)
        return self


@router.post("/search")
@access_log(action="READ", data_category="ORDER")
def search_orders(body: OrderSearch, request: Request, _operator: OrdersOperator) -> dict:
    """주문 검색 (v0.1 보강 E) — 조건은 본문, 접속기록에는 키 이름만 (app/admin_search.py)"""
    body.record_keys()
    o = orders.c
    conditions = kst_period(o.ordered_at, body.ordered_from, body.ordered_to)
    for column, value in (
        (o.id, body.order_id),
        (o.member_id, body.member_id),
        (o.status, body.status),
    ):
        if value is not None:
            conditions.append(column == value)
    with request.app.state.engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(orders).where(*conditions)
        ).scalar_one()
        rows = conn.execute(
            _list_query()
            .where(*conditions)
            .order_by(o.ordered_at.desc(), o.id.desc())
            .limit(body.size)
            .offset((body.page - 1) * body.size)
        ).mappings()
        items = [dict(r) for r in rows]
    record_subjects(dict.fromkeys(i["member_id"] for i in items if i["member_id"] is not None))
    return {"items": items, "page": body.page, "size": body.size, "total": total}


@router.get("/{order_id}")
@access_log(action="READ", data_category="ORDER")
def get_order(order_id: int, request: Request, _operator: OrdersOperator) -> dict:
    """주문 상세 — 배송 정보 포함 (운영팀 배송 업무, 7-4 ③). 주문 조회 접속기록 READ·ORDER

    배송 정보는 주문할 때 복사해 둔 값이다(회원의 현재 배송지가 아님). 탈퇴 회원의 주문은
    분리보관으로 옮겨져 비어 있다. 카드번호는 플랫폼에 없다 — 카드사·PG 거래번호뿐.
    처리한 정보주체 = 그 주문의 회원 (탈퇴로 끊긴 주문은 정보주체 없음).
    """
    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(
                select(
                    orders.c.id,
                    orders.c.member_id,
                    member.c.name.label("member_name"),
                    product.c.name.label("product_name"),
                    orders.c.amount,
                    orders.c.status,
                    orders.c.ordered_at,
                    *(orders.c[c] for c in SHIP_COLUMNS),
                    product.c.ship_days,
                    orders.c.courier,
                    orders.c.tracking_no,
                    orders.c.shipped_at,
                    orders.c.delivered_at,
                    orders.c.delivery_memo,
                    orders.c.courier_shipment_id,
                    orders.c.courier_status,
                    orders.c.courier_synced_at,
                    payment.c.method,
                    payment.c.card_company,
                    payment.c.card_number_masked,
                    payment.c.installment_months,
                    payment.c.approve_no,
                    payment.c.receipt_url,
                    payment.c.pg_tid,
                    payment.c.approved_at,
                    bank_deposit.c.bank_name.label("deposit_bank_name"),
                    bank_deposit.c.account_number.label("deposit_account_number"),
                    bank_deposit.c.account_holder.label("deposit_account_holder"),
                    bank_deposit.c.depositor_name,
                    bank_deposit.c.due_at.label("deposit_due_at"),
                    bank_deposit.c.confirmed_at.label("deposit_confirmed_at"),
                    bank_deposit.c.confirmed_by.label("deposit_confirmed_by"),
                )
                .select_from(
                    orders.join(product, product.c.id == orders.c.product_id)
                    .outerjoin(member, member.c.id == orders.c.member_id)
                    .outerjoin(payment, payment.c.order_id == orders.c.id)
                    .outerjoin(bank_deposit, bank_deposit.c.order_id == orders.c.id)
                )
                .where(orders.c.id == order_id)
            )
            .mappings()
            .first()
        )
        if row is not None:
            claims = claims_of(conn, [order_id]).get(order_id, [])
            history = history_of(conn, order_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "order not found")
    if row["member_id"] is not None:
        record_subjects([row["member_id"]])
    detail = dict(row)
    ship_days = detail.pop("ship_days")
    detail["expected_delivery_at"] = expected_delivery(detail["ordered_at"], ship_days)
    detail["return_deadline"] = withdrawal_deadline(detail["delivered_at"])
    detail["claims"] = claims
    detail["status_history"] = history
    return detail


def _locked(conn, order_id: int):
    order = lock_order(conn, order_id)
    if order["member_id"] is not None:
        record_subjects([order["member_id"]])
    return order


@router.post("/{order_id}/deposit-confirm")
@access_log(action="UPDATE", data_category="ORDER")
def deposit_confirm(order_id: int, request: Request, operator_: OrdersOperator) -> dict:
    """무통장입금 입금 확인 → 결제완료 (은행 연동 없음 — 입금자명·금액을 보고 관리자가 확인, F7)"""
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = _locked(conn, order_id)
        result = confirm_deposit(conn, order, now, operator_.id)
    if result == "EXPIRED":
        # 기한 경과 상태는 위 트랜잭션에서 커밋한 뒤 오류로 알린다
        raise ApiError(409, "DEPOSIT_EXPIRED", "deposit due date has passed")
    return {"id": order_id, "member_id": order["member_id"], "status": "PAID"}


@router.get("/{order_id}/refund-account")
@access_log(action="READ", data_category="PAYMENT")
def reveal_claim_refund_account(
    order_id: int, request: Request, _operator: RefundViewOperator
) -> dict:
    """이체 환불할 계좌 전체 번호 — 고객이 취소·반품할 때 고른 계좌의 사본 (F7-1)

    회원 상세의 환불계좌 전체 보기와 같이 데이터 유형 "결제수단"으로 기록 → Argus 결제수단 조회 룰.
    이체해야 하는 건(환불 대기)이 있을 때만 보여 준다 — 끝난 건의 계좌를 다시 열람할 이유가 없다.
    """
    with request.app.state.engine.connect() as conn:
        member_id = conn.execute(select(orders.c.member_id).where(orders.c.id == order_id)).scalar()
        if member_id is not None:
            record_subjects([member_id])
        claim = pending_refund_claim(conn, order_id)
    if claim is None or claim["refund_account_enc"] is None:
        raise ApiError(404, "NOT_FOUND", "no refund transfer is waiting")
    number = request.app.state.cipher.decrypt(
        claim["refund_account_enc"], claim_refund_context(order_id)
    )
    return {
        "bank_name": claim["refund_bank_name"],
        "account_holder": claim["refund_account_holder"],
        "account_number": number,
        "refund_amount": claim["refund_amount"],
    }


@router.post("/{order_id}/refund-complete")
@access_log(action="UPDATE", data_category="ORDER")
def refund_complete(order_id: int, request: Request, operator_: RefundViewOperator) -> dict:
    """이체 환불 완료 처리 — 관리자가 고객 환불계좌로 이체한 뒤 (무통장입금, F7)

    이체할 계좌를 볼 수 있는 역할(환불계좌 전체 보기 권한)만 처리한다.
    """
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        order = _locked(conn, order_id)
        complete_refund(conn, order, now, operator_.id)
        claim = conn.execute(
            select(order_claim.c.id, order_claim.c.refund_status)
            .where(order_claim.c.order_id == order_id)
            .order_by(order_claim.c.id.desc())
        ).first()
    return {"id": order_id, "claim_id": claim.id, "refund_status": claim.refund_status}
