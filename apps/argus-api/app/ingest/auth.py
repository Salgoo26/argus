"""시스템 간 인증 — HMAC-SHA256 서명 검증 (api-spec 1-2, CLAUDE.md 3절 #9)

검증 순서 (api-spec 1-2):
1. X-Argus-Source로 비밀키 조회 → 없으면 401
2. X-Argus-Timestamp가 현재 시각 ±300초 밖 → 401 (재전송 방지)
3. 본문을 최대 1MB까지만 읽음 → 초과 시 413
   (인증 전에 큰 본문을 끝까지 받지 않게 스트리밍으로 끊는다)
4. 서명을 상수 시간 비교(hmac.compare_digest) → 불일치 401

서명 대상은 `{timestamp}.{raw_body}` — 파싱 전의 원본 바이트다. JSON을 파싱했다 다시 직렬화하면
공백·키 순서가 달라져 서명이 맞지 않게 되므로, 검증이 끝난 원본 바이트를 그대로 넘겨준다.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import Request

from app.config import Settings
from app.errors import ApiError

TIMESTAMP_TOLERANCE_SEC = 300
MAX_BODY_BYTES = 1024 * 1024  # api-spec 1-1
SIGNATURE_PREFIX = "v1="


@dataclass(frozen=True)
class VerifiedRequest:
    source_code: str
    body: bytes


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """발신 측(relay·테스트·시드 스크립트)도 같은 함수로 서명한다."""
    mac = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256)
    return SIGNATURE_PREFIX + mac.hexdigest()


async def _read_body_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "request body exceeds 1MB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "request body exceeds 1MB")
        chunks.append(chunk)
    return b"".join(chunks)


async def verify_signed_request(request: Request) -> VerifiedRequest:
    settings: Settings = request.app.state.settings

    source = request.headers.get("x-argus-source", "")
    secret = settings.ingest_secret(source)
    if secret is None:
        raise ApiError(401, "UNKNOWN_SOURCE", "unknown source system")

    timestamp = request.headers.get("x-argus-timestamp", "")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > TIMESTAMP_TOLERANCE_SEC:
        raise ApiError(401, "INVALID_TIMESTAMP", "timestamp missing or outside ±300s")

    body = await _read_body_limited(request)

    provided = request.headers.get("x-argus-signature", "")
    expected = sign(secret, timestamp, body)
    # 상수 시간 비교 — 앞에서부터 몇 글자가 맞는지가 응답 시간으로 새지 않게
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise ApiError(401, "INVALID_SIGNATURE", "signature mismatch")

    return VerifiedRequest(source, body)
