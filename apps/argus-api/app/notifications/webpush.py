"""웹 푸시 (v0.1 보강 F-4) — Argus에 접속해 있지 않아도 브라우저가 켜져 있으면 받는 급한 알림

브라우저 표준 Web Push를 외부 라이브러리 없이 구현한다(이미 쓰는 cryptography·PyJWT만):
- VAPID(RFC 8292): 서버 신원 — ES256 JWT를 Authorization 헤더에 싣는다
- 내용 암호화(RFC 8291, aes128gcm RFC 8188): 브라우저가 준 공개키(p256dh)·인증 비밀(auth)로 암호화해
  푸시 서비스(브라우저 제조사 서버)는 내용을 볼 수 없다

그래도 푸시 내용에는 **회원번호·취급자 이름·룰 상세를 넣지 않는다** — "새 탐지건(심각도 상)" 수준과
탐지건 화면 주소만. 클릭하면 Argus 로그인을 거쳐 그 화면으로 간다.

안전장치
- 구독 주소(endpoint)는 알려진 브라우저 푸시 서비스의 https 주소만 받는다 — 서버가 사용자가 넣은
  임의 주소로 요청을 보내는 통로(SSRF)가 되지 않게
- VAPID 키가 없으면 웹 푸시만 꺼지고 화면 알림은 그대로 동작한다
- 발송은 한 번만 시도(화면 알림이 주 수단). 푸시 서비스가 404·410이면 구독을 지운다
"""

import base64
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Engine, delete, select, update

from app.models import argus_user, notification, push_subscription

logger = logging.getLogger(__name__)

# 브라우저 제조사 푸시 서비스 (Chrome·Edge=FCM, Firefox=Mozilla, Safari=Apple, 구형 Edge=WNS)
PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
)
PUSH_HOST_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")
RECORD_SIZE = 4096
TTL_SECONDS = 24 * 60 * 60  # 브라우저가 꺼져 있으면 푸시 서비스가 하루까지 보관
SEND_TIMEOUT = 10
BATCH = 100

KIND_TEXT = {
    "DETECTED": "새 탐지건이 있습니다",
    "REQUESTED": "소명 요청이 있습니다",
    "DUE_SOON": "소명 기한이 다가옵니다",
    "OVERDUE": "소명 기한이 지났습니다",
    "SUBMITTED": "소명이 제출됐습니다",
}
SEVERITY_TEXT = {"HIGH": "상", "MEDIUM": "중", "LOW": "하"}


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def is_push_endpoint(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and parts.port in (None, 443)
        and not parts.username
        and (host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES))
    )


@dataclass(frozen=True)
class VapidKeys:
    private_key: ec.EllipticCurvePrivateKey
    public_key: str  # base64url, 압축하지 않은 점 65바이트 — 브라우저 구독 때 applicationServerKey
    subject: str  # mailto: 또는 https: — 푸시 서비스가 문제 시 연락할 곳


def load_vapid(public_key: str, private_key: str, subject: str) -> VapidKeys | None:
    """둘 다 비면 웹 푸시 꺼짐(None). 하나만 있거나 짝이 맞지 않으면 기동 거부
    — 조용히 틀리지 않게"""
    if not public_key and not private_key:
        return None
    try:
        scalar = int.from_bytes(b64url_decode(private_key), "big")
        key = ec.derive_private_key(scalar, ec.SECP256R1())
    except ValueError as error:
        raise ValueError("ARGUS_VAPID_PRIVATE_KEY is not a P-256 private key") from error
    derived = b64url_encode(_raw_public(key.public_key()))
    if derived != public_key:
        raise ValueError("ARGUS_VAPID_PUBLIC_KEY does not match ARGUS_VAPID_PRIVATE_KEY")
    if not subject.startswith(("mailto:", "https://")):
        raise ValueError("ARGUS_VAPID_SUBJECT must be a mailto: or https: URL")
    return VapidKeys(key, public_key, subject)


def vapid_from(settings) -> VapidKeys | None:
    """설정(ARGUS_VAPID_*)에서 — argus-api·worker가 기동할 때 한 번"""
    private = settings.vapid_private_key.get_secret_value() if settings.vapid_private_key else ""
    return load_vapid(settings.vapid_public_key, private, settings.vapid_subject)


def generate_vapid() -> tuple[str, str]:
    """(공개키, 개인키) base64url — app.scripts.vapid_keys가 출력한다"""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url_encode(_raw_public(key.public_key())), b64url_encode(private)


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """RFC 8291 — aes128gcm 본문 하나(레코드 1개). salt·sender_key는 테스트 재현용"""
    ua_public_raw = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_raw)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    sender_public_raw = _raw_public(sender_key.public_key())
    salt = salt or os.urandom(16)

    ecdh_secret = sender_key.exchange(ec.ECDH(), ua_public)
    key_info = b"WebPush: info\x00" + ua_public_raw + sender_public_raw
    ikm = _hkdf(auth_secret, ecdh_secret, key_info, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 = 마지막 레코드
    header = salt + struct.pack("!IB", RECORD_SIZE, len(sender_public_raw)) + sender_public_raw
    return header + ciphertext


def vapid_authorization(endpoint: str, keys: VapidKeys, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(now or time.time()) + 12 * 60 * 60,
        "sub": keys.subject,
    }
    token = jwt.encode(claims, keys.private_key, algorithm="ES256")
    return f"vapid t={token}, k={keys.public_key}"


def push_payload(kind: str, severity: str, detection_id: int) -> dict:
    """푸시 내용 — 개인정보·룰 상세 없음. 탐지건 번호는 화면 주소에만"""
    return {
        "title": "Argus",
        "body": f"{KIND_TEXT[kind]} (심각도 {SEVERITY_TEXT[severity]})",
        "url": f"/detections/{detection_id}",
        "tag": f"argus-{detection_id}",
    }


Sender = Callable[[str, bytes, dict[str, str]], int]


def http_send(endpoint: str, body: bytes, headers: dict[str, str]) -> int:
    if not is_push_endpoint(endpoint):  # 저장 때 막지만, 보내기 직전에도 다시 확인
        raise ValueError("not a push service endpoint")
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")  # noqa: S310 — https 푸시 서비스만
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def send(subscription, payload: dict, keys: VapidKeys, sender: Sender = http_send) -> int:
    body = encrypt(
        json.dumps(payload, ensure_ascii=False).encode(),
        subscription.p256dh,
        subscription.auth,
    )
    headers = {
        "Authorization": vapid_authorization(subscription.endpoint, keys),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS),
        "Urgency": "high",
    }
    return sender(subscription.endpoint, body, headers)


def dispatch_pending(engine: Engine, keys: VapidKeys | None, sender: Sender = http_send) -> int:
    """발송 대기 알림을 웹 푸시로 — 보낸(2xx) 수를 돌려준다. 상태 변경 직후·worker 순찰마다 부른다

    같은 알림을 두 곳에서 동시에 보내지 않게 행을 잠그고(SKIP LOCKED) 가져온다.
    """
    sent = 0
    n, s = notification.c, push_subscription.c
    with engine.begin() as conn:
        rows = conn.execute(
            select(n.id, n.user_id, n.kind, n.severity, n.detection_id)
            .where(n.push_pending)
            .order_by(n.id)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        ).all()
        if not rows:
            return 0
        if keys is not None:
            subscriptions = conn.execute(
                select(s.id, s.user_id, s.endpoint, s.p256dh, s.auth)
                .join(argus_user, argus_user.c.id == s.user_id)
                .where(s.user_id.in_({r.user_id for r in rows}), argus_user.c.status == "ACTIVE")
            ).all()
            gone: set[int] = set()
            for row in rows:
                payload = push_payload(row.kind, row.severity, row.detection_id)
                for subscription in subscriptions:
                    if subscription.user_id != row.user_id or subscription.id in gone:
                        continue
                    try:
                        status = send(subscription, payload, keys, sender)
                    except Exception:
                        logger.warning("web push failed (subscription %s)", subscription.id)
                        continue
                    if status in (404, 410):  # 브라우저가 구독을 끊음
                        gone.add(subscription.id)
                    elif 200 <= status < 300:
                        sent += 1
                    else:
                        logger.warning("web push rejected: HTTP %s", status)
            if gone:
                conn.execute(delete(push_subscription).where(s.id.in_(gone)))
        # 한 번 시도하면 끝 — 키가 없어 꺼져 있어도 대기열에 쌓아 두지 않는다
        conn.execute(
            update(notification).where(n.id.in_([r.id for r in rows])).values(push_pending=False)
        )
    return sent
