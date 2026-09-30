"""Argus 로그인 — 담당자·취급자, 5회 잠금(LOCKED), 비활성·퇴직, 토큰 쿠키 (policy 4-3)"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select, text, update

from app.auth.deps import MAX_FAILED_LOGINS
from app.auth.passwords import hash_password
from app.auth.tokens import COOKIE_NAME
from app.models import argus_user, handler

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)  # argon2는 일부러 느리다 — 모듈에서 한 번만


@pytest.fixture(autouse=True)
def clean_users(admin_engine):
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))


def add_officer(admin_engine, password_hash, login_id="officer", **overrides) -> int:
    values = {"login_id": login_id, "password_hash": password_hash, "role": "OFFICER"}
    with admin_engine.begin() as conn:
        return conn.execute(
            insert(argus_user).values(**values | overrides).returning(argus_user.c.id)
        ).scalar_one()


def add_handler_account(admin_engine, password_hash, login_id="ops_park", terminated=False) -> int:
    with admin_engine.begin() as conn:
        handler_id = conn.execute(
            insert(handler)
            .values(
                source_system_id=1,
                login_id=login_id,
                name="박지훈",  # 가상 인물
                team="OPS",
                employment_status="TERMINATED" if terminated else "ACTIVE",
                terminated_at=datetime.now(UTC) if terminated else None,
                last_event_at=datetime.now(UTC),
            )
            .returning(handler.c.id)
        ).scalar_one()
        return conn.execute(
            insert(argus_user)
            .values(
                login_id=login_id,
                password_hash=password_hash,
                role="HANDLER",
                handler_id=handler_id,
            )
            .returning(argus_user.c.id)
        ).scalar_one()


def login(client, login_id="officer", password=PASSWORD):
    return client.post("/api/auth/login", json={"login_id": login_id, "password": password})


def account(admin_engine, login_id="officer") -> dict:
    with admin_engine.connect() as conn:
        return dict(
            conn.execute(select(argus_user).where(argus_user.c.login_id == login_id))
            .mappings()
            .one()
        )


# ── 성공 ──────────────────────────────────────────────────


def test_officer_login(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash)
    res = login(client)

    assert res.status_code == 200
    assert res.json() == {"login_id": "officer", "role": "OFFICER", "name": None}
    cookie = res.headers["set-cookie"].lower()
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=1800" in cookie
    assert account(admin_engine)["last_login_at"] is not None


def test_handler_login_shows_name_from_roster(client, admin_engine, password_hash):
    add_handler_account(admin_engine, password_hash)
    res = login(client, "ops_park")
    assert res.json() == {"login_id": "ops_park", "role": "HANDLER", "name": "박지훈"}


def test_me_returns_current_user_and_refreshes_cookie(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash)
    login(client)
    res = client.get("/api/auth/me")
    assert res.json()["login_id"] == "officer"
    assert res.headers["set-cookie"].startswith(f"{COOKIE_NAME}=")  # 30분 연장
    assert res.headers["cache-control"] == "no-store"


# ── 실패·잠금 ─────────────────────────────────────────────


def test_unknown_id_and_wrong_password_look_the_same(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash)
    wrong = login(client, password="wrong-password")
    unknown = login(client, login_id="nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_five_failures_lock_the_account(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash)
    for _ in range(MAX_FAILED_LOGINS):
        login(client, password="wrong-password")

    assert account(admin_engine)["status"] == "LOCKED"
    res = login(client)  # 맞는 비밀번호여도 거부
    assert res.status_code == 403 and res.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_locked_status_is_hidden_from_wrong_password(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash, status="LOCKED")
    assert login(client, password="wrong-password").json()["error"]["code"] == (
        "INVALID_CREDENTIALS"
    )


def test_success_resets_failed_count(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash)
    login(client, password="wrong-password")
    login(client)
    assert account(admin_engine)["failed_login_count"] == 0


@pytest.mark.parametrize("terminated", [False, True], ids=["disabled-account", "terminated"])
def test_disabled_or_terminated_handler_cannot_log_in(
    client, admin_engine, password_hash, terminated
):
    add_handler_account(admin_engine, password_hash, terminated=terminated)
    if not terminated:
        with admin_engine.begin() as conn:
            conn.execute(update(argus_user).values(status="DISABLED"))
    res = login(client, "ops_park")
    assert res.status_code == 403 and res.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_synced_handler_without_password_cannot_log_in(client, admin_engine):
    # 동기화로 생긴 A5 계정은 무작위 해시 — 관리 스크립트로 비밀번호를 설정하기 전엔 로그인 불가
    from app.auth.passwords import unusable_password_hash

    add_handler_account(admin_engine, unusable_password_hash())
    assert login(client, "ops_park").status_code == 401


# ── 토큰 ──────────────────────────────────────────────────


def test_api_requires_login(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401 and res.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("change", [{"status": "LOCKED"}, {"status": "DISABLED"}])
def test_session_is_cut_when_account_is_blocked(client, admin_engine, password_hash, change):
    add_officer(admin_engine, password_hash)
    login(client)
    with admin_engine.begin() as conn:
        conn.execute(update(argus_user).values(**change))
    assert client.get("/api/auth/me").status_code == 401  # 토큰 만료를 기다리지 않는다


def test_session_is_cut_when_handler_is_terminated(client, admin_engine, password_hash):
    add_handler_account(admin_engine, password_hash)
    login(client, "ops_park")
    with admin_engine.begin() as conn:
        conn.execute(
            update(handler).values(employment_status="TERMINATED", terminated_at=datetime.now(UTC))
        )
    assert client.get("/api/auth/me").status_code == 401


def test_forged_token_is_rejected(client, admin_engine, password_hash):
    import jwt

    user_id = add_officer(admin_engine, password_hash)
    forged = jwt.encode(
        {"sub": str(user_id), "iat": 0, "exp": 9999999999}, "attacker-key-0123456789abcdef-0123"
    )
    client.cookies.set(COOKIE_NAME, forged)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie(client, admin_engine, password_hash):
    add_officer(admin_engine, password_hash)
    login(client)
    res = client.post("/api/auth/logout")
    assert res.status_code == 204 and "max-age=0" in res.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").status_code == 401
