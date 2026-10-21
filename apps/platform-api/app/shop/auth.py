"""고객 회원가입·로그인·로그아웃 (PLT-01, PLT-02)

가입 항목 (커머스 확장 2026-10-21, 판단기록 F1·F2): 아이디(중복 확인)·비밀번호·이메일(인증)·
휴대폰 본인확인 결과 — 모두 필수. 이름·휴대폰은 본인확인 결과로 정해지고(직접 입력 칸 없음),
CI·DI·생년월일·성별·내외국인·통신사도 본인확인 결과대로 저장한다
(F1 — 최소수집 검토는 이후 영향평가).
인증은 shop/verification.py의 서명 토큰으로 증명한다 — 인증 없이 가입 API를 직접 부를 수 없다.
만 14세 미만은 본인확인 생년월일로 가입을 거부한다. 같은 사람(CI)은 한 계정만.
주소는 회원 정보가 아니라 배송지(마이페이지에서 등록)다.
요청에 다른 칸이 와도 모델에 없으면 버려진다.

회원가입: 필수 동의(이용약관·개인정보 수집이용·만 14세 이상)가 모두 있어야 가입된다.
선택 동의(마케팅)는 하지 않아도 가입된다 — 선택 항목 미동의를 이유로 가입을 거부하지 않는다
(PIPA §22⑤). 동의·미동의 모두 member_consent에 항목·버전·시각·IP로 남긴다.

로그인 실패 (기능 레이어 7 결정 5): 5회 연속 실패 → 15분 잠금, 시간이 지나면 자동 해제.
비밀번호 찾기가 없어 관리자처럼 영구 잠금하면 고객이 스스로 풀 방법이 없다.
계정 열거 방지·잠김은 비밀번호가 맞을 때만 알림 — 관리자 로그인(policy 4-3)과 같은 원칙.
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import dummy_password_hash, hash_password, verify_password
from app.auth.tokens import CUSTOMER, clear_session_cookie, issue_token, set_session_cookie
from app.config import Settings
from app.errors import ApiError
from app.models import consent_item, member, member_consent
from app.shop.deps import client_ip
from app.shop.validation import NewPassword, Password
from app.shop.verification import (
    EMAIL_AUDIENCE,
    IDENTITY_AUDIENCE,
    LoginId,
    read_verification,
)

router = APIRouter(prefix="/shop")

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)


MIN_AGE = 14


class SignupRequest(BaseModel):
    login_id: LoginId
    password: NewPassword
    email_token: str  # 이메일 인증 결과 (POST /shop/verifications/email)
    identity_token: str  # 휴대폰 본인확인 결과 (POST /shop/verifications/identity)
    # 항목 코드 → 동의 여부. 빠진 항목은 미동의
    consents: dict[str, bool]


class LoginRequest(BaseModel):
    login_id: Annotated[str, Field(min_length=1, max_length=40)]
    password: Password


def _age(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _consent_items(conn) -> list[dict]:
    """받는 동의 항목 — 받지 않기로 한 항목(active=false, 0010)은 가입 화면·검증에서 빠진다"""
    rows = conn.execute(
        select(consent_item)
        .where(consent_item.c.active)
        .order_by(consent_item.c.required.desc(), consent_item.c.code)
    ).mappings()
    return [dict(r) for r in rows]


def _login_response(response: Response, request: Request, member_id: int, login_id: str, name: str):
    settings: Settings = request.app.state.settings
    token = issue_token(
        member_id, request.app.state.auth_secret, settings.session_idle_minutes, CUSTOMER
    )
    set_session_cookie(response, token, settings, CUSTOMER)
    return {"login_id": login_id, "name": name}


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
    secret: bytes = request.app.state.auth_secret
    email = read_verification(body.email_token, secret, EMAIL_AUDIENCE)
    identity = read_verification(body.identity_token, secret, IDENTITY_AUDIENCE)
    birth = date.fromisoformat(identity["birth_date"])
    with request.app.state.engine.begin() as conn:
        items = _consent_items(conn)
        now = conn.execute(select(func.now())).scalar_one()
        unknown = set(body.consents) - {item["code"] for item in items}
        if unknown:
            raise ApiError(400, "BAD_REQUEST", "unknown consent item")
        if any(item["required"] and not body.consents.get(item["code"]) for item in items):
            raise ApiError(400, "REQUIRED_CONSENT_MISSING", "required consent missing")
        if _age(birth, now.date()) < MIN_AGE:
            raise ApiError(400, "UNDER_14", "members must be 14 or older")
        if conn.execute(select(member.c.id).where(member.c.ci == identity["ci"])).first():
            raise ApiError(409, "ALREADY_REGISTERED", "this person already has an account")

        try:
            with conn.begin_nested():
                member_id = conn.execute(
                    insert(member)
                    .values(
                        login_id=body.login_id,
                        email=email["email"],
                        email_verified_at=datetime.fromisoformat(email["verified_at"]),
                        password_hash=hash_password(body.password),
                        name=identity["name"],
                        phone=identity["phone"],
                        ci=identity["ci"],
                        di=identity["di"],
                        birth_date=birth,
                        gender=identity["gender"],
                        is_foreigner=identity["is_foreigner"],
                        carrier=identity["carrier"],
                        identity_verified_at=datetime.fromisoformat(identity["verified_at"]),
                    )
                    .returning(member.c.id)
                ).scalar_one()
        except IntegrityError as exc:
            # 인증 뒤 다른 가입이 먼저 끝난 경우 — 어느 유일 제약에 걸렸는지로 구분
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
            if "login_id" in constraint:
                raise ApiError(409, "LOGIN_ID_TAKEN", "login id already taken") from None
            if "ci" in constraint:
                raise ApiError(409, "ALREADY_REGISTERED", "already has an account") from None
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
    return _login_response(response, request, member_id, body.login_id, identity["name"])


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    # 결과를 트랜잭션 안에서 정하고 오류는 커밋 뒤에 던진다 (실패 횟수 증가가 롤백되지 않게)
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        row = (
            conn.execute(
                select(member)
                .where(member.c.login_id == body.login_id.strip().lower())
                .with_for_update()
            )
            .mappings()
            .first()
        )

        if row is None:
            verify_password(dummy_password_hash(), body.password)  # 응답 시간 균일화
            error = ApiError(401, "INVALID_CREDENTIALS", "invalid login id or password")
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
                error = ApiError(401, "INVALID_CREDENTIALS", "invalid login id or password")
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
    return _login_response(response, request, row["id"], row["login_id"], row["name"])


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    clear_session_cookie(response, request.app.state.settings, CUSTOMER)
