"""마이페이지 — 내 정보 열람·정정, 비밀번호 변경, 선택 동의 철회·재동의, 탈퇴 (PLT-03)

주소는 회원 정보가 아니라 배송지다(여러 개 — shop/addresses.py, 2026-10-07).

정보주체의 권리(PIPA §35 열람, §36 정정, §37 처리정지·동의 철회)를 화면에서 바로 행사하게 한다.
고객 본인 행위라 접속기록 대상이 아니다 (CLAUDE.md 3절 #4).
"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import CUSTOMER, clear_session_cookie
from app.errors import ApiError
from app.models import consent_item, member, member_consent
from app.shop.deps import CurrentMember, client_ip
from app.shop.validation import Name, NewPassword, Password, Phone
from app.shop.withdrawal import destroy_member

router = APIRouter(prefix="/shop/me")


class ProfileUpdate(BaseModel):
    """정정할 수 있는 칸만 — 이메일은 로그인 아이디라 칸 자체가 없다 (보내도 버려진다)"""

    name: Name
    phone: Phone


class PasswordChange(BaseModel):
    current_password: Password
    new_password: NewPassword


class ConsentChange(BaseModel):
    agreed: bool


class WithdrawRequest(BaseModel):
    password: Password


def _current_consents(conn, member_id: int) -> list[dict]:
    """받는 항목별 가장 최근 동의·철회 상태 (받지 않기로 한 항목의 지난 이력은 DB에만 — 0010)"""
    ranked = (
        select(
            member_consent.c.item_code,
            member_consent.c.item_version,
            member_consent.c.agreed,
            member_consent.c.acted_at,
            func.row_number()
            .over(
                partition_by=member_consent.c.item_code,
                order_by=(member_consent.c.acted_at.desc(), member_consent.c.id.desc()),
            )
            .label("rn"),
        )
        .where(member_consent.c.member_id == member_id)
        .subquery()
    )
    latest = select(ranked).where(ranked.c.rn == 1).subquery()
    rows = conn.execute(
        select(
            consent_item.c.code,
            consent_item.c.name,
            consent_item.c.required,
            latest.c.item_version,
            latest.c.agreed,
            latest.c.acted_at,
        )
        .select_from(consent_item.outerjoin(latest, latest.c.item_code == consent_item.c.code))
        .where(consent_item.c.active)
        .order_by(consent_item.c.required.desc(), consent_item.c.code)
    ).mappings()
    return [
        {
            "code": r["code"],
            "name": r["name"],
            "required": r["required"],
            "version": r["item_version"],
            "agreed": bool(r["agreed"]),
            "acted_at": r["acted_at"],
        }
        for r in rows
    ]


def _profile(conn, member_id: int) -> dict:
    row = (
        conn.execute(
            select(
                member.c.id,
                member.c.email,
                member.c.name,
                member.c.phone,
                member.c.created_at,
            ).where(member.c.id == member_id)
        )
        .mappings()
        .one()
    )
    return {**dict(row), "consents": _current_consents(conn, member_id)}


@router.get("")
def get_me(request: Request, me: CurrentMember) -> dict:
    with request.app.state.engine.connect() as conn:
        return _profile(conn, me.id)


@router.patch("")
def update_me(body: ProfileUpdate, request: Request, me: CurrentMember) -> dict:
    """정정 (§36). 이메일은 로그인 아이디라 바꾸지 않는다"""
    with request.app.state.engine.begin() as conn:
        conn.execute(
            update(member).where(member.c.id == me.id).values(name=body.name, phone=body.phone)
        )
        return _profile(conn, me.id)


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, request: Request, me: CurrentMember) -> None:
    with request.app.state.engine.begin() as conn:
        current = conn.execute(
            select(member.c.password_hash).where(member.c.id == me.id)
        ).scalar_one()
        if not verify_password(current, body.current_password):
            raise ApiError(400, "WRONG_PASSWORD", "current password does not match")
        conn.execute(
            update(member)
            .where(member.c.id == me.id)
            .values(password_hash=hash_password(body.new_password))
        )


@router.put("/consents/{code}")
def change_consent(code: str, body: ConsentChange, request: Request, me: CurrentMember) -> dict:
    """선택 동의의 철회·재동의 (§37). 필수 동의는 철회 대신 탈퇴로 — 서비스 계약의 전제다"""
    with request.app.state.engine.begin() as conn:
        item = (
            conn.execute(
                select(consent_item).where(consent_item.c.code == code, consent_item.c.active)
            )
            .mappings()
            .first()
        )
        if item is None:
            raise ApiError(404, "NOT_FOUND", "consent item not found")
        if item["required"]:
            raise ApiError(400, "REQUIRED_CONSENT", "required consent cannot be withdrawn")
        # 이력은 덮어쓰지 않고 쌓는다 — 언제 동의했고 언제 철회했는지가 증적
        conn.execute(
            insert(member_consent).values(
                member_id=me.id,
                item_code=code,
                item_version=item["version"],
                agreed=body.agreed,
                acted_at=func.now(),
                client_ip=client_ip(request),
                method="WEB_FORM",
            )
        )
        return _profile(conn, me.id)


@router.post("/withdraw", status_code=204)
def withdraw(body: WithdrawRequest, request: Request, response: Response, me: CurrentMember):
    """탈퇴 — 비밀번호 재확인 후 즉시 파기 (shop/withdrawal.py)"""
    with request.app.state.engine.begin() as conn:
        current = conn.execute(
            select(member.c.password_hash).where(member.c.id == me.id).with_for_update()
        ).scalar_one()
        if not verify_password(current, body.password):
            raise ApiError(400, "WRONG_PASSWORD", "password does not match")
        destroy_member(conn, me.id)
    # 미들웨어가 연장 토큰을 다시 심지 않게
    request.state.session_cookie = None
    clear_session_cookie(response, request.app.state.settings, CUSTOMER)
