"""관리자 로그인 — 실패 제한, 계정 열거 방지, 토큰 쿠키, 퇴직·잠금 즉시 반영"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select, update

from app.auth.deps import MAX_FAILED_LOGINS
from app.auth.tokens import COOKIE_NAME
from app.models import operator

from conftest import TEST_AUTH_SECRET, add_operator, login


def _failed_count(engine, login_id: str = "ops_park") -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(operator.c.failed_login_count).where(operator.c.login_id == login_id)
        ).scalar_one()


def _set_cookie_header(res) -> str:
    return next(v for k, v in res.headers.multi_items() if k == "set-cookie")


# ── 로그인 성공 ───────────────────────────────────────────


def test_login_sets_httponly_strict_cookie(client, engine, password_hash):
    add_operator(engine, password_hash)
    res = login(client)

    assert res.status_code == 200
    assert res.json()["must_change_password"] is False
    assert {k: res.json()[k] for k in ("login_id", "name", "team", "role")} == {
        "login_id": "ops_park",
        "name": "박지훈",
        "team": "OPS",
        "role": "OPS",
    }
    cookie = _set_cookie_header(res).lower()
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "max-age=1800" in cookie  # 30분 미사용 만료


def test_login_response_does_not_include_password_hash(client, engine, password_hash):
    add_operator(engine, password_hash)
    assert "argon2" not in login(client).text


def test_successful_login_resets_failed_count(client, engine, password_hash):
    add_operator(engine, password_hash)
    login(client, password="wrong-password")
    login(client, password="wrong-password")
    assert _failed_count(engine) == 2

    assert login(client).status_code == 200
    assert _failed_count(engine) == 0


# ── 실패·잠금 ─────────────────────────────────────────────


def test_unknown_id_and_wrong_password_look_the_same(client, engine, password_hash):
    add_operator(engine, password_hash)
    wrong_password = login(client, password="wrong-password")
    unknown_id = login(client, login_id="nobody")

    # 계정 열거 방지 — 상태 코드·본문이 똑같다
    assert wrong_password.status_code == unknown_id.status_code == 401
    assert wrong_password.json() == unknown_id.json()
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_account_locks_after_five_failures(client, engine, password_hash):
    add_operator(engine, password_hash)
    for _ in range(MAX_FAILED_LOGINS):
        assert login(client, password="wrong-password").status_code == 401

    res = login(client)  # 맞는 비밀번호여도 거부

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_LOCKED"
    assert COOKIE_NAME not in res.cookies


def test_locked_status_is_hidden_from_wrong_password(client, engine, password_hash):
    add_operator(engine, password_hash, failed_login_count=MAX_FAILED_LOGINS)
    # 비밀번호를 모르는 사람에게는 "잠김"을 알려주지 않는다
    assert login(client, password="wrong-password").json()["error"]["code"] == (
        "INVALID_CREDENTIALS"
    )


def test_terminated_operator_cannot_log_in(client, engine, password_hash):
    add_operator(
        engine, password_hash, employment_status="TERMINATED", terminated_at=datetime.now(UTC)
    )
    res = login(client)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.parametrize(
    "body",
    [
        {"login_id": "ops_park"},
        {"login_id": "", "password": "x"},
        {"login_id": "ops_park", "password": "x" * 257},
    ],
)
def test_malformed_login_is_400_without_echo(client, body):
    res = client.post("/admin/auth/login", json=body)
    assert res.status_code == 400
    assert res.json() == {"error": {"code": "BAD_REQUEST", "message": "malformed request"}}


# ── 토큰 ──────────────────────────────────────────────────


def test_admin_route_requires_login(client):
    res = client.get("/admin/members")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHENTICATED"


def _forged(claims: dict, secret: str = TEST_AUTH_SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=algorithm)


@pytest.mark.parametrize(
    "make_token",
    [
        # 다른 키로 서명
        lambda oid, now: _forged(
            {"sub": str(oid), "iat": now, "exp": now + timedelta(minutes=5)},
            secret="attacker-secret-attacker-secret-0000",
        ),
        # 만료
        lambda oid, now: _forged(
            {"sub": str(oid), "iat": now - timedelta(hours=1), "exp": now - timedelta(minutes=1)}
        ),
        # 서명 없음 (alg=none) — 헤더의 alg를 믿으면 뚫린다
        lambda oid, now: jwt.encode(
            {"sub": str(oid), "iat": now, "exp": now + timedelta(minutes=5)}, None, "none"
        ),
        # 만료(exp) 클레임 없음
        lambda oid, now: _forged({"sub": str(oid), "iat": now}),
        lambda oid, now: "not-a-jwt",
    ],
    ids=["wrong-key", "expired", "alg-none", "no-exp", "garbage"],
)
def test_invalid_token_is_rejected(client, engine, password_hash, make_token):
    operator_id = add_operator(engine, password_hash)
    client.cookies.set(COOKIE_NAME, make_token(operator_id, datetime.now(UTC)))
    assert client.get("/admin/members").status_code == 401


def test_request_refreshes_session_cookie(client, engine, password_hash):
    add_operator(engine, password_hash)
    login(client)

    res = client.get("/admin/members")

    assert res.status_code == 200
    assert _set_cookie_header(res).startswith(f"{COOKIE_NAME}=")  # 만료 연장된 새 토큰


@pytest.mark.parametrize(
    "change",
    [
        {"employment_status": "TERMINATED", "terminated_at": datetime.now(UTC)},
        {"failed_login_count": MAX_FAILED_LOGINS},
    ],
    ids=["terminated", "locked"],
)
def test_existing_session_is_cut_when_operator_is_blocked(client, engine, password_hash, change):
    add_operator(engine, password_hash)
    login(client)
    assert client.get("/admin/members").status_code == 200

    with engine.begin() as conn:
        conn.execute(update(operator).where(operator.c.login_id == "ops_park").values(**change))

    assert client.get("/admin/members").status_code == 401  # 토큰 만료를 기다리지 않는다


def test_logout_clears_cookie(client, engine, password_hash):
    add_operator(engine, password_hash)
    login(client)

    res = client.post("/admin/auth/logout")

    assert res.status_code == 204
    assert "max-age=0" in _set_cookie_header(res).lower()
    assert client.get("/admin/members").status_code == 401


def test_admin_responses_are_not_cached(client, engine, password_hash):
    add_operator(engine, password_hash)
    login(client)
    assert client.get("/admin/members").headers["cache-control"] == "no-store"


def test_me_returns_current_operator(client, engine, password_hash):
    add_operator(engine, password_hash)
    assert client.get("/admin/auth/me").status_code == 401
    login(client)
    res = client.get("/admin/auth/me")
    assert res.json()["must_change_password"] is False
    assert {k: res.json()[k] for k in ("login_id", "name", "team", "role")} == {
        "login_id": "ops_park",
        "name": "박지훈",
        "team": "OPS",
        "role": "OPS",
    }
