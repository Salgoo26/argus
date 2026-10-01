"""고객 회원가입·로그인·로그아웃 (PLT-01, PLT-02)

회원가입: 필수 동의(이용약관·개인정보 수집이용·만 14세 이상)가 모두 있어야 가입된다.
선택 동의(마케팅)는 하지 않아도 가입된다 — 선택 항목 미동의를 이유로 가입을 거부하지 않는다
(PIPA §22⑤). 동의·미동의 모두 member_consent에 항목·버전·시각·IP로 남긴다.

로그인 실패 (기능 레이어 7 결정 5): 5회 연속 실패 → 15분 잠금, 시간이 지나면 자동 해제.
비밀번호 찾기가 없어 관리자처럼 영구 잠금하면 고객이 스스로 풀 방법이 없다.
계정 열거 방지·잠김은 비밀번호가 맞을 때만 알림 — 관리자 로그인(policy 4-3)과 같은 원칙.
"""

from datetime import timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import dummy_password_hash, hash_password, verify_password
from app.auth.tokens import CUSTOMER, clear_session_cookie, issue_token, set_session_cookie
from app.config import Settings
from app.errors import ApiError
from app.models import consent_item, member, member_consent
from app.shop.deps import client_ip
from app.shop.validation import Address, Email, Name, NewPassword, Password, Phone

router = APIRouter(prefix="/shop")

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)


class SignupRequest(BaseModel):
    email: Email
    password: NewPassword
    name: Name
    phone: Phone = None
    address: Address = None
    # 항목 코드 → 동의 여부. 빠진 항목은 미동의
    consents: dict[str, bool]


class LoginRequest(BaseModel):
    email: Email
    password: Password


def _consent_items(conn) -> list[dict]:
    rows = conn.execute(
        select(consent_item).order_by(consent_item.c.required.desc(), consent_item.c.code)
    ).mappings()
    return [dict(r) for r in rows]


def _login_response(response: Response, request: Request, member_id: int, email: str, name: str):
    settings: Settings = request.app.state.settings
    token = issue_token(
        member_id, request.app.state.auth_secret, settings.session_idle_minutes, CUSTOMER
    )
    set_session_cookie(response, token, settings, CUSTOMER)
    return {"email": email, "name": name}


@router.get("/consent-items")
def list_consent_items(request: Request) -> list[dict]:
    """회원가입 화면의 동의 문구 — 목적·항목·기간을 알리고 받는다 (§15②)"""
    with request.app.state.engine.connect() as conn:
        items = _consent_items(conn)
    return [
        {
            k: item[k]
            for k in ("code", "name", "required", "version", "purpose", "items", "retention")
        }
        for item in items
    ]


@router.post("/auth/signup", status_code=201)
def signup(body: SignupRequest, request: Request, response: Response) -> dict:
    ip = client_ip(request)
    with request.app.state.engine.begin() as conn:
        items = _consent_items(conn)
        now = conn.execute(select(func.now())).scalar_one()
        unknown = set(body.consents) - {item["code"] for item in items}
        if unknown:
            raise ApiError(400, "BAD_REQUEST", "unknown consent item")
        if any(item["required"] and not body.consents.get(item["code"]) for item in items):
            raise ApiError(400, "REQUIRED_CONSENT_MISSING", "required consent missing")

        try:
            with conn.begin_nested():
                member_id = conn.execute(
                    insert(member)
                    .values(
                        email=body.email,
                        password_hash=hash_password(body.password),
                        name=body.name,
                        phone=body.phone,
                        address=body.address,
                    )
                    .returning(member.c.id)
                ).scalar_one()
        except IntegrityError:
            # 알려진 한계: 이메일 인증이 없어 가입 응답으로 이메일 존재 여부를 알 수 있다
            raise ApiError(409, "EMAIL_TAKEN", "email already registered") from None

        conn.execute(
            insert(member_consent),
            [
                {
                    "member_id": member_id,
                    "item_code": item["code"],
                    "item_version": item["version"],
                    "agreed": bool(body.consents.get(item["code"])),
                    "acted_at": now,
                    "client_ip": ip,
                    "method": "WEB_FORM",
                }
                for item in items
            ],
        )
    return _login_response(response, request, member_id, body.email, body.name)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    # 결과를 트랜잭션 안에서 정하고 오류는 커밋 뒤에 던진다 (실패 횟수 증가가 롤백되지 않게)
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        row = (
            conn.execute(select(member).where(member.c.email == body.email).with_for_update())
            .mappings()
            .first()
        )

        if row is None:
            verify_password(dummy_password_hash(), body.password)  # 응답 시간 균일화
            error = ApiError(401, "INVALID_CREDENTIALS", "invalid email or password")
        else:
            locked = row["locked_until"] is not None and row["locked_until"] > now
            ok = verify_password(row["password_hash"], body.password)
            if not ok:
                if not locked:  # 잠긴 동안의 실패는 세지 않는다 — 잠금이 끝없이 연장되지 않게
                    failed = row["failed_login_count"] + 1
                    values = (
                        {"failed_login_count": 0, "locked_until": now + LOCK_DURATION}
                        if failed >= MAX_FAILED_LOGINS
                        else {"failed_login_count": failed}
                    )
                    conn.execute(update(member).where(member.c.id == row["id"]).values(**values))
                error = ApiError(401, "INVALID_CREDENTIALS", "invalid email or password")
            elif locked:
                error = ApiError(403, "ACCOUNT_LOCKED", "account is temporarily locked")
            else:
                conn.execute(
                    update(member)
                    .where(member.c.id == row["id"])
                    .values(failed_login_count=0, locked_until=None)
                )
                error = None

    if error is not None:
        raise error
    return _login_response(response, request, row["id"], row["email"], row["name"])


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    clear_session_cookie(response, request.app.state.settings, CUSTOMER)
