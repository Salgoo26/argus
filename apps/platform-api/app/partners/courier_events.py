"""가상 택배 → 쇼핑몰 상태 통지 수신 — POST /partners/courier/events

택배사가 상태(집하·이동·배송완료, 반품 회수)가 바뀔 때마다 서명해서 보낸다.
- 서명: X-Courier-Signature = v1= + hex(HMAC-SHA256(키, "{X-Courier-Timestamp}.{본문}")),
  상수 시간 비교, timestamp ±300초 (수집 API와 같은 규칙 — CLAUDE.md 3절 #9)
- 통지는 접수 건의 현재 상태 전체(스냅샷)라 같은 통지가 두 번 와도 결과가 같다
- 시스템 간 통신이라 취급자 행위가 아니다 → 접속기록 대상 아님(/admin 밖)
"""

import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.errors import ApiError
from app.fulfillment import apply_shipment

router = APIRouter(prefix="/partners/courier")

MAX_SKEW_SEC = 300
MAX_BODY_BYTES = 64 * 1024


def verify_signature(secret: bytes, timestamp: str, signature: str, body: bytes) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_SKEW_SEC:
        return False
    expected = (
        "v1=" + hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(signature.encode(), expected.encode())


@router.post("/events")
async def courier_event(request: Request) -> dict:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "event too large")
    if not verify_signature(
        request.app.state.courier_webhook_secret,
        request.headers.get("x-courier-timestamp", ""),
        request.headers.get("x-courier-signature", ""),
        body,
    ):
        raise ApiError(401, "INVALID_SIGNATURE", "signature mismatch or stale timestamp")
    try:
        shipment = json.loads(body)["shipment"]
        if not isinstance(shipment, dict) or not isinstance(shipment.get("shipmentId"), str):
            raise ValueError
    except (ValueError, KeyError, TypeError) as error:
        raise ApiError(400, "BAD_REQUEST", "malformed event") from error
    with request.app.state.engine.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        result = apply_shipment(conn, shipment, now)
    # unknown·stale·ignored도 200 — 택배사가 같은 통지를 끝없이 다시 보내지 않게
    return {"result": result}
