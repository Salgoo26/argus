"""웹 푸시 (v0.1 보강 F-4) — 암호화·VAPID·구독 주소 제한·발송·구독 API

실제 푸시 서비스로는 보내지 않는다 — 발송 함수(sender)를 모의 객체로 바꿔 끼운다.
암호화는 브라우저(구독자) 입장에서 RFC 8291대로 다시 풀어 확인한다.
"""

import json
import struct
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.main import create_app
from app.models import argus_user, detection, handler, notification, push_subscription
from app.notifications.events import PUSH, notify
from app.notifications.webpush import (
    b64url_decode,
    b64url_encode,
    dispatch_pending,
    encrypt,
    generate_vapid,
    is_push_endpoint,
    load_vapid,
    push_payload,
    vapid_authorization,
)

from conftest import TEST_CLIENT_ADDR

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
ENDPOINT = "https://fcm.googleapis.com/fcm/send/test-subscription-1"
PUBLIC, PRIVATE = generate_vapid()  # 테스트마다 새로 만든 일회용 키 (레포에 키 없음)
KEYS = load_vapid(PUBLIC, PRIVATE, "mailto:privacy-officer@example.com")


# ── 구독자(브라우저) 흉내 ──────────────────────────────────


class Browser:
    """구독할 때 브라우저가 만드는 키 쌍과 인증 비밀 — 받은 푸시를 RFC 8291대로 푼다"""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public_raw = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.auth = b"0123456789abcdef"
        self.p256dh = b64url_encode(self.public_raw)
        self.auth_b64 = b64url_encode(self.auth)

    def decrypt(self, body: bytes) -> bytes:
        salt, (record_size, idlen) = body[:16], struct.unpack("!IB", body[16:21])
        sender_raw, ciphertext = body[21 : 21 + idlen], body[21 + idlen :]
        assert record_size == 4096 and idlen == 65
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_raw)
        secret = self.key.exchange(ec.ECDH(), sender)

        def hkdf(salt_, ikm, info, length):
            return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt_, info=info).derive(ikm)

        ikm = hkdf(self.auth, secret, b"WebPush: info\x00" + self.public_raw + sender_raw, 32)
        cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
        assert plain.endswith(b"\x02")  # 마지막 레코드 구분자
        return plain[:-1]


def test_encrypted_body_decrypts_on_the_browser_side():
    browser = Browser()
    body = encrypt(b'{"title":"Argus"}', browser.p256dh, browser.auth_b64)
    assert browser.decrypt(body) == b'{"title":"Argus"}'
    # 같은 내용도 보낼 때마다 다른 암호문(무작위 salt·일회용 키)
    assert encrypt(b"x", browser.p256dh, browser.auth_b64) != encrypt(
        b"x", browser.p256dh, browser.auth_b64
    )


def test_vapid_authorization_header():
    header = vapid_authorization(ENDPOINT, KEYS, now=1_800_000_000)
    token = header.split("t=")[1].split(",")[0]
    assert header.endswith(f"k={PUBLIC}")
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(PUBLIC))
    claims = jwt.decode(
        token,
        public,
        algorithms=["ES256"],
        audience="https://fcm.googleapis.com",
        options={"verify_exp": False},
    )
    assert claims["sub"] == "mailto:privacy-officer@example.com"
    assert claims["exp"] == 1_800_000_000 + 12 * 3600


def test_vapid_keys_must_be_a_matching_pair():
    assert load_vapid("", "", "mailto:a@example.com") is None  # 둘 다 비면 꺼짐
    other_public, _ = generate_vapid()
    for public, private, subject in (
        (PUBLIC, "", "mailto:a@example.com"),
        (other_public, PRIVATE, "mailto:a@example.com"),
        (PUBLIC, PRIVATE, "javascript:alert(1)"),
    ):
        with pytest.raises(ValueError):
            load_vapid(public, private, subject)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (ENDPOINT, True),
        ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
        ("https://web.push.apple.com/QGuQ", True),
        ("https://db5p.notify.windows.com/w/?token=abc", True),
        ("http://fcm.googleapis.com/fcm/send/x", False),  # https만
        ("https://fcm.googleapis.com:8443/x", False),
        ("https://user@fcm.googleapis.com/x", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("https://argus-db:5432/x", False),  # 내부망으로 요청하게 만들 수 없다 (SSRF)
        ("https://169.254.169.254/latest/meta-data", False),
    ],
)
def test_only_browser_push_services_are_allowed(url, ok):
    assert is_push_endpoint(url) is ok


def test_payload_has_no_personal_or_rule_details():
    payload = push_payload("DETECTED", "HIGH", 42)
    assert payload == {
        "title": "Argus",
        "body": "새 탐지건이 있습니다 (심각도 상)",
        "url": "/detections/42",
        "tag": "argus-42",
    }


def test_push_is_only_for_urgent_kinds():
    assert ("DETECTED", "HIGH") in PUSH and ("DETECTED", "MEDIUM") not in PUSH
    assert ("REQUESTED", "HIGH") in PUSH and ("REQUESTED", "LOW") not in PUSH
    assert ("OVERDUE", "MEDIUM") in PUSH and ("OVERDUE", "LOW") not in PUSH
    assert not any(kind == "SUBMITTED" for kind, _ in PUSH)


# ── 발송 (DB) ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def cleanup(admin_engine):
    yield
    with admin_engine.begin() as conn:
        for table in ("push_subscription", "notification", "detection", "argus_user", "handler"):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름


def _user(admin_engine, login_id="officer", status="ACTIVE") -> int:
    with admin_engine.begin() as conn:
        return conn.execute(
            insert(argus_user)
            .values(
                login_id=login_id,
                password_hash=hash_password(PASSWORD),
                role="OFFICER",
                status=status,
            )
            .returning(argus_user.c.id)
        ).scalar_one()


def _case(admin_engine, severity="HIGH") -> int:
    now = datetime.now(UTC)
    with admin_engine.begin() as conn:
        return conn.execute(
            insert(detection)
            .values(
                rule_id=1,
                rule_version=1,
                rule_snapshot={"name": "대량 다운로드", "version": 1},
                source_system_id=1,
                access_path="APP",
                actor_login_id="ops_park",
                group_bucket="2026-10-10",
                severity=severity,
                first_occurred_at=now,
                last_occurred_at=now,
            )
            .returning(detection.c.id)
        ).scalar_one()


def _subscribe(admin_engine, user_id: int, browser: Browser, endpoint=ENDPOINT) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            insert(push_subscription).values(
                user_id=user_id, endpoint=endpoint, p256dh=browser.p256dh, auth=browser.auth_b64
            )
        )


class Recorder:
    def __init__(self, status: int = 201):
        self.status, self.calls = status, []

    def __call__(self, endpoint, body, headers):
        self.calls.append((endpoint, body, headers))
        return self.status


def _pending(app_engine) -> list[bool]:
    with app_engine.connect() as conn:
        return list(conn.execute(select(notification.c.push_pending)).scalars())


def test_urgent_notification_is_pushed_once_with_encrypted_payload(app_engine, admin_engine):
    browser = Browser()
    user_id = _user(admin_engine)
    _subscribe(admin_engine, user_id, browser)
    case = _case(admin_engine)
    with app_engine.begin() as conn:
        notify(conn, [user_id], "DETECTED", case, "HIGH")
        notify(conn, [user_id], "SUBMITTED", case, "HIGH", 1)  # 화면만
    assert sorted(_pending(app_engine)) == [False, True]

    recorder = Recorder()
    assert dispatch_pending(app_engine, KEYS, recorder) == 1
    [(endpoint, body, headers)] = recorder.calls
    assert endpoint == ENDPOINT
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Authorization"].startswith(
        "vapid t="
    )
    payload = json.loads(browser.decrypt(body))
    assert payload == push_payload("DETECTED", "HIGH", case)
    assert _pending(app_engine) == [False, False]
    assert dispatch_pending(app_engine, KEYS, recorder) == 0 and len(recorder.calls) == 1  # 한 번만


def test_gone_subscription_is_deleted(app_engine, admin_engine):
    user_id = _user(admin_engine)
    _subscribe(admin_engine, user_id, Browser())
    case = _case(admin_engine)
    with app_engine.begin() as conn:
        notify(conn, [user_id], "DETECTED", case, "HIGH")
    dispatch_pending(app_engine, KEYS, Recorder(status=410))
    with app_engine.connect() as conn:
        assert conn.execute(select(push_subscription)).first() is None


def test_without_keys_push_is_off_and_nothing_piles_up(app_engine, admin_engine):
    user_id = _user(admin_engine)
    _subscribe(admin_engine, user_id, Browser())
    case = _case(admin_engine)
    with app_engine.begin() as conn:
        notify(conn, [user_id], "DETECTED", case, "HIGH")
    recorder = Recorder()
    assert dispatch_pending(app_engine, None, recorder) == 0
    assert recorder.calls == [] and _pending(app_engine) == [False]


def test_disabled_account_gets_no_push(app_engine, admin_engine):
    user_id = _user(admin_engine, status="DISABLED")
    _subscribe(admin_engine, user_id, Browser())
    case = _case(admin_engine)
    with app_engine.begin() as conn:
        notify(conn, [user_id], "DETECTED", case, "HIGH")
    recorder = Recorder()
    dispatch_pending(app_engine, KEYS, recorder)
    assert recorder.calls == []


def test_sender_failure_does_not_break_dispatch(app_engine, admin_engine):
    user_id = _user(admin_engine)
    _subscribe(admin_engine, user_id, Browser())
    case = _case(admin_engine)
    with app_engine.begin() as conn:
        notify(conn, [user_id], "DETECTED", case, "HIGH")

    def broken(*_args):
        raise TimeoutError("push service down")

    assert dispatch_pending(app_engine, KEYS, broken) == 0
    assert _pending(app_engine) == [False]  # 화면 알림이 주 수단 — 푸시는 재시도하지 않는다


# ── 구독 API ─────────────────────────────────────────────


@pytest.fixture
def push_app(settings):
    application = create_app(
        settings.model_copy(
            update={"vapid_public_key": PUBLIC, "vapid_private_key": SecretStr(PRIVATE)}
        )
    )
    yield application
    application.state.engine.dispose()


def _login(application, admin_engine) -> tuple[TestClient, int]:
    user_id = _user(admin_engine)
    client = TestClient(application, client=TEST_CLIENT_ADDR)
    res = client.post("/api/auth/login", json={"login_id": "officer", "password": PASSWORD})
    assert res.status_code == 200
    return client, user_id


def _body(browser: Browser, endpoint=ENDPOINT) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": browser.p256dh, "auth": browser.auth_b64}}


def test_config_and_subscribe(push_app, admin_engine, app_engine):
    client, user_id = _login(push_app, admin_engine)
    assert client.get("/api/push/config").json() == {"enabled": True, "public_key": PUBLIC}
    assert client.post("/api/push/subscriptions", json=_body(Browser())).status_code == 204
    assert client.post("/api/push/subscriptions", json=_body(Browser())).status_code == 204  # 갱신
    with app_engine.connect() as conn:
        rows = conn.execute(select(push_subscription.c.user_id)).scalars().all()
    assert rows == [user_id]


@pytest.mark.parametrize(
    "body",
    [
        {
            "endpoint": "https://argus-api:8000/api/x",
            "keys": {"p256dh": "A" * 87, "auth": "A" * 22},
        },
        {"endpoint": ENDPOINT, "keys": {"p256dh": "short", "auth": "A" * 22}},
        {"endpoint": ENDPOINT, "keys": {"p256dh": "A" * 87, "auth": "A"}},
    ],
)
def test_invalid_subscription_is_400(push_app, admin_engine, body):
    client, _ = _login(push_app, admin_engine)
    assert client.post("/api/push/subscriptions", json=body).status_code == 400


def test_logout_and_unsubscribe_remove_subscriptions(push_app, admin_engine, app_engine):
    client, _ = _login(push_app, admin_engine)
    client.post("/api/push/subscriptions", json=_body(Browser()))
    other = "https://updates.push.services.mozilla.com/wpush/v2/second"
    client.post("/api/push/subscriptions", json=_body(Browser(), other))
    assert client.post("/api/push/unsubscribe", json={"endpoint": other}).status_code == 204
    with app_engine.connect() as conn:
        assert conn.execute(select(push_subscription.c.endpoint)).scalars().all() == [ENDPOINT]
    client.post("/api/auth/logout")
    with app_engine.connect() as conn:
        assert conn.execute(select(push_subscription)).first() is None  # 로그아웃하면 끊는다


def test_subscribe_is_refused_when_push_is_off(client, admin_engine):
    _user(admin_engine)
    client.post("/api/auth/login", json={"login_id": "officer", "password": PASSWORD})
    assert client.get("/api/push/config").json() == {"enabled": False, "public_key": None}
    assert client.post("/api/push/subscriptions", json=_body(Browser())).status_code == 409


def test_terminated_handler_loses_subscriptions(admin_engine, app_engine, client):
    from app.handlers.sync import _sync_a5_account
    from app.ingest.handler_validation import HandlerState

    now = datetime.now(UTC)
    with admin_engine.begin() as conn:
        handler_id = conn.execute(
            insert(handler)
            .values(
                source_system_id=1,
                login_id="ops_park",
                name="박지훈",
                team="OPS",
                employment_status="ACTIVE",
                last_event_at=now,
            )
            .returning(handler.c.id)
        ).scalar_one()
        user_id = conn.execute(
            insert(argus_user)
            .values(login_id="ops_park", password_hash="x", role="HANDLER", handler_id=handler_id)
            .returning(argus_user.c.id)
        ).scalar_one()
    _subscribe(admin_engine, user_id, Browser())
    with app_engine.begin() as conn:
        _sync_a5_account(
            conn,
            handler_id,
            HandlerState("ops_park", "박지훈", "OPS", "TERMINATED", now, now),
        )
    with app_engine.connect() as conn:
        assert conn.execute(select(push_subscription)).first() is None
