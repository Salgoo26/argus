"""회원 목록·검색·상세·다운로드·결제수단 조회 (PLT-11, PLT-13, PLT-16) — 감시 대상 관리자 기능

접속기록: 각 라우트의 @access_log 문패 + record_subjects(조회·다운로드한 회원 PK).
"""

import csv
import io
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import StringConstraints, model_validator
from sqlalchemy import func, select

from app.admin_search import SearchPage, SearchText, check_period, kst_period
from app.agent import access_log, record_subjects
from app.auth.deps import PERMISSIONS, ExportOperator, MembersOperator, RefundViewOperator
from app.crypto import refund_account_context
from app.errors import ApiError
from app.models import member, orders, payment, product, refund_account
from app.shop.refund import masked_view

router = APIRouter(prefix="/admin/members")

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 오프셋으로 충분
EXPORT_MAX_ROWS = 10_000
# 주소는 회원 정보가 아니라 배송지(2026-10-07) — 회원 목록 파일에는 담지 않는다
CSV_COLUMNS = ("id", "name", "email", "phone", "joined_at")
# 엑셀이 수식으로 해석하는 시작 문자 (OWASP "CSV Injection")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    # 셀이 수식으로 실행되지 않도록 앞에 '를 붙여 문자열로 고정
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


@router.get("")
@access_log(action="READ", data_category="MEMBER_BASIC")
def list_members(
    request: Request,
    _operator: MembersOperator,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(member)).scalar_one()
        rows = conn.execute(
            select(
                member.c.id,
                member.c.name,
                member.c.email,
                member.c.phone,
                member.c.status,
                member.c.created_at,
            )
            .order_by(member.c.id)
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [dict(r) for r in rows]
    # 화면에 표시된 회원 = 처리한 정보주체 (api-spec 2-4 "회원 목록 조회, 20건 표시")
    record_subjects(item["id"] for item in items)
    return {"items": items, "page": page, "size": size, "total": total}


class MemberSearch(SearchPage):
    name: SearchText | None = None
    email: SearchText | None = None
    # 하이픈은 있어도 없어도 된다 — 숫자만 비교
    phone: (
        Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9][0-9-]{0,19}$")]
        | None
    ) = None
    status: Literal["ACTIVE", "WITHDRAWN"] | None = None
    joined_from: date | None = None  # 한국 날짜, 양 끝 포함
    joined_to: date | None = None

    @model_validator(mode="after")
    def _period(self):
        check_period(self.joined_from, self.joined_to)
        return self


@router.post("/search")
@access_log(action="READ", data_category="MEMBER_BASIC")
def search_members(body: MemberSearch, request: Request, _operator: MembersOperator) -> dict:
    """회원 검색 (v0.1 보강 E) — 조건은 본문, 접속기록에는 키 이름만 (app/admin_search.py)"""
    body.record_keys()
    m = member.c
    conditions = kst_period(m.created_at, body.joined_from, body.joined_to)
    # 부분 일치 — 입력의 % _ 는 글자 그대로 (autoescape)
    if body.name:
        conditions.append(m.name.contains(body.name, autoescape=True))
    if body.email:
        conditions.append(m.email.contains(body.email, autoescape=True))
    if body.phone:
        digits = body.phone.replace("-", "")
        conditions.append(func.replace(m.phone, "-", "").contains(digits, autoescape=True))
    if body.status:
        conditions.append(m.status == body.status)

    with request.app.state.engine.connect() as conn:
        # FROM을 명시한다 — 조건이 없으면 테이블 없는 count(*)가 되어 늘 1이 나온다
        total = conn.execute(
            select(func.count()).select_from(member).where(*conditions)
        ).scalar_one()
        rows = conn.execute(
            select(m.id, m.name, m.email, m.phone, m.status, m.created_at)
            .where(*conditions)
            .order_by(m.id)
            .limit(body.size)
            .offset((body.page - 1) * body.size)
        ).mappings()
        items = [dict(r) for r in rows]
    # 결과로 화면에 보인 회원 전부 = 처리한 정보주체 (0건이어도 기록)
    record_subjects(item["id"] for item in items)
    return {"items": items, "page": body.page, "size": body.size, "total": total}


@router.get("/export")
@access_log(action="DOWNLOAD", data_category="MEMBER_BASIC")
def export_members(
    request: Request,
    _operator: ExportOperator,
    joined_from: date | None = None,
    joined_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=EXPORT_MAX_ROWS)] = EXPORT_MAX_ROWS,
) -> Response:
    """활동 중인 회원을 가입일 조건으로 골라 CSV로 내려준다.

    joined_from·joined_to는 한국 날짜 기준이고 양 끝을 포함한다.
    """
    query = select(
        member.c.id,
        member.c.name,
        member.c.email,
        member.c.phone,
        member.c.created_at,
    ).where(member.c.status == "ACTIVE")
    if joined_from is not None:
        query = query.where(member.c.created_at >= datetime.combine(joined_from, time(), KST))
    if joined_to is not None:
        next_day = datetime.combine(joined_to + timedelta(days=1), time(), KST)
        query = query.where(member.c.created_at < next_day)

    with request.app.state.engine.connect() as conn:
        rows = conn.execute(query.order_by(member.c.id).limit(limit)).all()
    # 파일에 담긴 회원 전원 = 처리한 정보주체. 1,000명이 넘으면 Agent가 잘라 보내고 건수는 전체
    record_subjects(row[0] for row in rows)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for member_id, name, email, phone, created_at in rows:
        joined_at = created_at.astimezone(KST).isoformat(timespec="seconds")
        writer.writerow([member_id, *map(_csv_safe, (name, email, phone)), joined_at])

    filename = f"members_{datetime.now(UTC).astimezone(KST):%Y%m%d_%H%M%S}.csv"
    return Response(
        # BOM(utf-8-sig): 엑셀이 한글을 깨뜨리지 않고 UTF-8로 인식하게
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _member_or_404(conn, member_id: int):
    row = (
        conn.execute(
            select(
                member.c.id,
                member.c.name,
                member.c.email,
                member.c.phone,
                member.c.status,
                member.c.created_at,
            ).where(member.c.id == member_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "member not found")
    return row


@router.get("/{member_id}")
@access_log(action="READ", data_category="MEMBER_BASIC")
def get_member(member_id: int, request: Request, operator_: MembersOperator) -> dict:
    """회원 상세 (PLT-11) — 환불계좌는 끝 4자리만. 전체 번호는 아래 별도 조회(결제수단)

    배송지는 싣지 않는다 — 배송지 마스킹·전체 보기는 v0.2 (db-schema 4절 플랫폼 보강).
    배송 업무에 필요한 주소는 주문 상세(주문에 복사된 배송 정보)에서 본다
    """
    # 대상이 정해진 조회는 업무 로직보다 먼저 기록 — 없는 회원이어도 "누구를 보려 했는지"가 남는다
    record_subjects([member_id])
    with request.app.state.engine.connect() as conn:
        profile = _member_or_404(conn, member_id)
        account = (
            conn.execute(select(refund_account).where(refund_account.c.member_id == member_id))
            .mappings()
            .first()
        )
        # 주문 권한이 없는 역할(마케팅)에게는 회원의 주문을 싣지 않는다 (v0.1 보강 L-1)
        recent_orders = (
            _recent_orders(conn, member_id) if operator_.role in PERMISSIONS["ORDERS"] else None
        )
    return {
        **dict(profile),
        "refund_account": masked_view(account),
        "orders": recent_orders,
    }


def _recent_orders(conn, member_id: int) -> list[dict]:
    rows = conn.execute(
        select(
            orders.c.id,
            product.c.name.label("product_name"),
            orders.c.amount,
            orders.c.status,
            orders.c.ordered_at,
            payment.c.card_company,
        )
        .select_from(
            orders.join(product, product.c.id == orders.c.product_id).outerjoin(
                payment, payment.c.order_id == orders.c.id
            )
        )
        .where(orders.c.member_id == member_id)
        .order_by(orders.c.ordered_at.desc())
        .limit(20)
    ).mappings()
    return [dict(r) for r in rows]


@router.get("/{member_id}/refund-account")
@access_log(action="READ", data_category="PAYMENT")
def reveal_refund_account(member_id: int, request: Request, _operator: RefundViewOperator) -> dict:
    """환불계좌 전체 번호 — 데이터 유형 "결제수단"으로 기록 → Argus 결제수단 조회 룰(상)로 항상 탐지

    화면의 "전체 보기"를 눌렀을 때만 부른다(기능 레이어 7 결정 7). 복호화는 이 요청 안에서만 하고
    응답 외에는 어디에도(로그 포함) 남기지 않는다.
    """
    record_subjects([member_id])
    with request.app.state.engine.connect() as conn:
        account = (
            conn.execute(select(refund_account).where(refund_account.c.member_id == member_id))
            .mappings()
            .first()
        )
    if account is None:
        raise ApiError(404, "NOT_FOUND", "refund account not found")
    number = request.app.state.cipher.decrypt(
        account["account_number_enc"], refund_account_context(member_id)
    )
    return {
        "bank_name": account["bank_name"],
        "account_holder": account["account_holder"],
        "account_number": number,
    }
