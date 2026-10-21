"""회원가입 인증 — 이메일 인증·휴대폰 본인확인(가상) (커머스 확장, 판단기록 F1·F2)

데모라 실제 발송·실제 본인확인은 없다. 대신 "인증을 마쳤다"는 결과를 서명 토큰으로 돌려주고,
회원가입은 그 토큰이 있어야만 받는다 — 인증 단계를 건너뛰고 가입 API를 직접 부를 수 없게.

이메일 인증: 인증 버튼을 누르면 바로 인증 완료(실제 메일 발송·인증번호 입력 없음).
이미 가입된 이메일이면 여기서 알린다(알려진 한계: 이메일 존재 여부가 드러난다 — 기존과 같음).

휴대폰 본인확인(가상 본인확인기관):
- 실제 서비스라면 고객은 본인확인기관(통신사 등) 창에서 통신사·이름·주민번호 앞 7자리·휴대폰을
  입력하고, 쇼핑몰은 기관이 돌려준 **결과**(CI·DI·이름·생년월일·성별·내외국인·휴대폰·통신사)만
  받는다.
  쇼핑몰은 주민등록번호를 처리할 수 없다(개인정보 보호법 제24조의2)
- 이 데모의 가상 본인확인 창도 주민번호 앞 7자리를 **창 안에서만** 생년월일·성별·내외국인으로 바꾸고
  번호 자체는 보내지 않는다. 그래서 이 API에는 주민번호를 받는 칸이 없다(보내도 버려진다)
  — PG 결제창의 카드번호와 같은 구조(shop/orders.py)
- 본인확인 이용 동의는 본인확인기관의 동의라 그 창에서만 받는다(쇼핑몰 member_consent에 남기지 않음)
- CI·DI는 가상 기관이 이름·생년월일·성별로 만든 값이다(실제 CI는 주민번호 기반 — 기관만 안다)

토큰: 관리자·고객 로그인과 같은 키로 서명하되 audience를 따로 둔다 — 로그인 토큰으로 쓸 수 없다.
"""

import base64
import hashlib
import hmac
import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select

from app.errors import ApiError
from app.models import member
from app.shop.validation import Email, Name, Phone

router = APIRouter(prefix="/shop")

ALGORITHM = "HS256"
EMAIL_AUDIENCE = "shop-email-verification"
IDENTITY_AUDIENCE = "shop-identity-verification"
TOKEN_TTL = timedelta(minutes=30)
CARRIERS = ("SKT", "KT", "LGU+", "SKT알뜰폰", "KT알뜰폰", "LGU+알뜰폰")

_LOGIN_ID = re.compile(r"^[a-z][a-z0-9]{3,19}$")


def _login_id(value: str) -> str:
    value = value.strip().lower()
    if not _LOGIN_ID.match(value):
        raise ValueError("login id must be 4-20 lowercase letters/digits, starting with a letter")
    return value


LoginId = Annotated[str, Field(min_length=1, max_length=40), AfterValidator(_login_id)]


def _issue(claims: dict, secret: bytes, audience: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {**claims, "aud": audience, "iat": now, "exp": now + TOKEN_TTL}, secret, algorithm=ALGORITHM
    )


def read_verification(token: str, secret: bytes, audience: str) -> dict:
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=audience,
            options={"require": ["aud", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        raise ApiError(400, "VERIFICATION_REQUIRED", f"{audience} token invalid") from None


class EmailVerificationRequest(BaseModel):
    email: Email


class IdentityResult(BaseModel):
    """가상 본인확인 창이 보내는 값 — 주민번호 칸은 없다 (모듈 설명)"""

    carrier: Literal[CARRIERS]  # type: ignore[valid-type]
    name: Name
    birth_date: date
    gender: Literal["M", "F"]
    is_foreigner: bool
    phone: Phone
    # 본인확인기관 이용 동의(창 안의 체크) — 동의하지 않으면 기관이 인증하지 않는다
    agreed: bool


@router.get("/auth/login-id-available")
def login_id_available(login_id: str, request: Request) -> dict:
    """아이디 중복 확인 (알려진 한계: 요청 횟수 제한 없음 — 아이디 존재 여부가 드러난다)"""
    try:
        normalized = _login_id(login_id)
    except ValueError:
        raise ApiError(400, "BAD_REQUEST", "invalid login id") from None
    with request.app.state.engine.connect() as conn:
        taken = conn.execute(select(member.c.id).where(member.c.login_id == normalized)).first()
    return {"login_id": normalized, "available": taken is None}


@router.post("/verifications/email")
def verify_email(body: EmailVerificationRequest, request: Request) -> dict:
    """이메일 인증 — 데모라 버튼을 누르면 바로 인증 완료"""
    with request.app.state.engine.connect() as conn:
        taken = conn.execute(select(member.c.id).where(member.c.email == body.email)).first()
    if taken is not None:
        raise ApiError(409, "EMAIL_TAKEN", "email already registered")
    verified_at = datetime.now(UTC)
    token = _issue(
        {"email": body.email, "verified_at": verified_at.isoformat()},
        request.app.state.auth_secret,
        EMAIL_AUDIENCE,
    )
    return {"email": body.email, "verified_at": verified_at, "token": token}


def mock_ci(secret: bytes, name: str, birth: date, gender: str) -> str:
    """가상 연계정보(CI) — 같은 사람(이름·생년월일·성별)이면 늘 같은 88자 값"""
    key = hmac.new(secret, b"mock-identity-agency/ci", hashlib.sha256).digest()
    digest = hmac.new(key, f"{name}|{birth.isoformat()}|{gender}".encode(), hashlib.sha512)
    return base64.b64encode(digest.digest()).decode()


def mock_di(secret: bytes, ci: str) -> str:
    """가상 중복가입확인정보(DI) — 사이트마다 다른 64자 값"""
    key = hmac.new(secret, b"mock-identity-agency/di", hashlib.sha256).digest()
    return hmac.new(key, f"{ci}|argus-shop".encode(), hashlib.sha256).hexdigest()


@router.post("/verifications/identity")
def verify_identity(body: IdentityResult, request: Request) -> dict:
    """가상 본인확인기관 — 입력하면 바로 인증 성공, 결과를 서명 토큰으로 돌려준다"""
    if not body.agreed:
        raise ApiError(400, "IDENTITY_CONSENT_REQUIRED", "identity verification consent required")
    secret: bytes = request.app.state.auth_secret
    ci = mock_ci(secret, body.name, body.birth_date, body.gender)
    result = {
        "ci": ci,
        "di": mock_di(secret, ci),
        "name": body.name,
        "birth_date": body.birth_date.isoformat(),
        "gender": body.gender,
        "is_foreigner": body.is_foreigner,
        "phone": body.phone,
        "carrier": body.carrier,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    token = _issue(result, secret, IDENTITY_AUDIENCE)
    # 화면에는 확인용으로 이름·휴대폰·생년월일만 — CI·DI는 토큰 안에만
    return {
        "name": body.name,
        "phone": body.phone,
        "birth_date": body.birth_date,
        "token": token,
    }
