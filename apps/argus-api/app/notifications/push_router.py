"""웹 푸시 구독 API (v0.1 보강 F-4)

- GET  /api/push/config          웹 푸시를 쓸 수 있는지 + VAPID 공개키(브라우저 구독에 필요)
- POST /api/push/subscriptions   이 브라우저의 구독 등록 (같은 주소면 갱신 — 주인도 지금 사용자로)
- POST /api/push/unsubscribe     이 브라우저의 구독 삭제

구독 주소는 알려진 브라우저 푸시 서비스의 https 주소만 받는다(webpush.is_push_endpoint)
— 서버가 사용자가 넣은 임의 주소로 요청을 보내지 않게.
로그아웃하면 그 사용자의 구독을 모두 지운다(auth/router).
자체 접속기록 제외: 본인 브라우저 설정이고 정보주체 처리가 없다.
"""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import AfterValidator, BaseModel, StringConstraints
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agent import access_log_exempt
from app.auth.deps import CurrentUser
from app.errors import ApiError
from app.models import push_subscription
from app.notifications.webpush import b64url_decode, is_push_endpoint

router = APIRouter(prefix="/api/push")

_EXEMPT = "본인 브라우저의 웹 푸시 설정 — 정보주체 처리 없음"


def _endpoint(value: str) -> str:
    if not is_push_endpoint(value):
        raise ValueError("endpoint must be a browser push service https URL")
    return value


def _key_of(length: int):
    def check(value: str) -> str:
        try:
            decoded = b64url_decode(value)
        except ValueError:
            decoded = b""
        if len(decoded) != length:
            raise ValueError(f"must be base64url of {length} bytes")
        return value

    return check


Endpoint = Annotated[str, StringConstraints(max_length=1000), AfterValidator(_endpoint)]
B64 = StringConstraints(pattern=r"^[A-Za-z0-9_-]+={0,2}$", max_length=128)


class SubscriptionKeys(BaseModel):
    p256dh: Annotated[str, B64, AfterValidator(_key_of(65))]  # P-256 공개키(압축 안 함)
    auth: Annotated[str, B64, AfterValidator(_key_of(16))]


class SubscriptionBody(BaseModel):
    endpoint: Endpoint
    keys: SubscriptionKeys


class UnsubscribeBody(BaseModel):
    endpoint: Endpoint


@router.get("/config")
@access_log_exempt(_EXEMPT)
def push_config(request: Request, _user: CurrentUser) -> dict:
    vapid = request.app.state.vapid
    return {"enabled": vapid is not None, "public_key": vapid.public_key if vapid else None}


@router.post("/subscriptions", status_code=204)
@access_log_exempt(_EXEMPT)
def subscribe(body: SubscriptionBody, request: Request, user: CurrentUser) -> None:
    if request.app.state.vapid is None:
        raise ApiError(409, "PUSH_DISABLED", "web push is not configured")
    values = {
        "user_id": user.id,
        "endpoint": body.endpoint,
        "p256dh": body.keys.p256dh,
        "auth": body.keys.auth,
    }
    with request.app.state.engine.begin() as conn:
        # 같은 브라우저에 다른 사람이 로그인하면 그 사람의 구독이 된다(앞 사람에게 가지 않게)
        conn.execute(
            pg_insert(push_subscription)
            .values(**values)
            .on_conflict_do_update(index_elements=["endpoint"], set_=values)
        )


@router.post("/unsubscribe", status_code=204)
@access_log_exempt(_EXEMPT)
def unsubscribe(body: UnsubscribeBody, request: Request, user: CurrentUser) -> None:
    with request.app.state.engine.begin() as conn:
        conn.execute(
            delete(push_subscription).where(
                push_subscription.c.endpoint == body.endpoint,
                push_subscription.c.user_id == user.id,
            )
        )
