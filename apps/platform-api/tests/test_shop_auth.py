"""고객 회원가입·로그인 — 동의 이력, 15분 잠금, 고객·관리자 토큰 분리, 접속기록 미대상"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, inspect, select, update

from app.auth.tokens import ADMIN, CUSTOMER, issue_token
from app.models import consent_item, member, member_consent, shipping_address

from conftest import (
    CUSTOMER_PASSWORD,
    REQUIRED_CONSENTS,
    TEST_AUTH_SECRET,
    TEST_CLIENT_ADDR,
    add_operator,
    login,
    outbox_payloads,
    shop_login,
    signup,
)

WRONG = "wrong-password-1"


def _consents(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(member_consent).order_by(member_consent.c.item_code))
        return [dict(r) for r in rows.mappings()]


def _member_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(member)).scalar_one()


# ── 동의 항목 ─────────────────────────────────────────────


def test_consent_items_show_purpose_items_and_retention(client):
    items = client.get("/shop/consent-items").json()

    # 계약 이행 항목은 동의 대신 안내 (§15①4·§22③, 0010) — 개인정보 수집·이용 (필수) 동의는 없다
    assert {i["code"] for i in items} == {"TOS", "AGE_OVER_14", "MARKETING"}
    marketing = next(i for i in items if i["code"] == "MARKETING")
    assert marketing["required"] is False
    # §15② — 목적·항목·기간을 알리고 받는다
    assert all(i["purpose"] and i["items"] and i["retention"] and i["version"] for i in items)


# ── 회원가입 ─────────────────────────────────────────────


def test_signup_records_every_consent_with_version_time_and_ip(client, engine):
    res = signup(client, consents={**REQUIRED_CONSENTS, "MARKETING": True})

    assert res.status_code == 201
    assert res.cookies.get(CUSTOMER.cookie_name)  # 가입 후 바로 로그인
    rows = _consents(engine)
    assert {r["item_code"]: r["agreed"] for r in rows} == {
        "AGE_OVER_14": True,
        "MARKETING": True,
        "TOS": True,
    }
    # 동의 당시 버전 — 이용약관은 v2 (0010)
    versions = {r["item_code"]: r["item_version"] for r in rows}
    assert versions == {
        "AGE_OVER_14": "v1",
        "MARKETING": "v1",
        "TOS": "v2",
    }
    assert all(r["acted_at"] for r in rows)
    assert all(str(r["client_ip"]) == TEST_CLIENT_ADDR[0] for r in rows)


def test_optional_consent_is_not_required_and_refusal_is_recorded(client, engine):
    res = signup(client, consents=REQUIRED_CONSENTS)  # 마케팅 빠짐

    assert res.status_code == 201
    marketing = next(r for r in _consents(engine) if r["item_code"] == "MARKETING")
    assert marketing["agreed"] is False


@pytest.mark.parametrize("missing", ["TOS", "AGE_OVER_14"])
def test_signup_without_required_consent_is_rejected(client, engine, missing):
    res = signup(client, consents={**REQUIRED_CONSENTS, missing: False})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "REQUIRED_CONSENT_MISSING"
    assert _member_count(engine) == 0


def test_password_is_stored_as_argon2id_hash(client, engine):
    signup(client)
    with engine.connect() as conn:
        stored = conn.execute(select(member.c.password_hash)).scalar_one()
    assert stored.startswith("$argon2id$") and CUSTOMER_PASSWORD not in stored


def test_duplicate_email_is_rejected_case_insensitively(client):
    assert signup(client, email="buyer@example.com").status_code == 201
    res = signup(client, email="  Buyer@Example.com ")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    "overrides",
    [
        {"password": "short1"},  # 10자 미만
        {"password": "onlyletterslong"},  # 1종류
        {"email": "not-an-email"},
        {"name": "   "},
        {"phone": "02-123-4567"},  # 휴대전화 형식 아님
        {"phone": ""},  # 휴대폰 필수 (2026-10-07)
        {"phone": None},
        {"consents": {**REQUIRED_CONSENTS, "UNKNOWN": True}},
    ],
    ids=[
        "short",
        "one-kind",
        "email",
        "blank-name",
        "phone",
        "no-phone",
        "null-phone",
        "unknown-consent",
    ],
)
def test_invalid_signup_input_is_rejected(client, engine, overrides):
    assert signup(client, **overrides).status_code == 400
    assert _member_count(engine) == 0


def test_signup_without_phone_field_is_rejected(client, engine):
    body = {
        "email": "buyer@example.com",
        "password": CUSTOMER_PASSWORD,
        "name": "구매자",
        "consents": REQUIRED_CONSENTS,
    }
    assert client.post("/shop/auth/signup", json=body).status_code == 400
    assert _member_count(engine) == 0


def test_signup_collects_only_the_required_items(client, engine):
    """생년월일·성별·주소는 받지 않는다 (policy 4-3 최소수집) — 보내도 저장할 칸이 없다"""
    res = signup(client, birth_date="1990-01-01", gender="F", address="서울특별시 가상구 가상로 1")
    assert res.status_code == 201
    columns = {c["name"] for c in inspect(engine).get_columns("member")}
    # 주소는 회원 정보가 아니라 배송지 (0007) — member에 칸이 없고, 가입으로 배송지도 안 생긴다
    assert not columns & {"birth_date", "birthday", "gender", "sex", "address"}
    with engine.connect() as conn:
        row = conn.execute(select(member)).mappings().one()
        assert conn.execute(select(func.count()).select_from(shipping_address)).scalar_one() == 0
    assert "1990" not in str(dict(row)) and "가상로" not in str(dict(row))


def test_retired_privacy_consent_is_rejected_but_its_history_is_kept(client, engine):
    """받지 않게 된 개인정보 수집·이용 (필수) 동의(0010) — 항목 행은 지난 동의 이력의 증적으로 남고,
    보내면 모르는 항목으로 거부된다(가입 화면이 이 항목을 보여 주지 않으므로)"""
    res = signup(client, consents={**REQUIRED_CONSENTS, "PRIVACY_REQUIRED": True})
    assert res.status_code == 400
    assert _member_count(engine) == 0
    with engine.connect() as conn:
        row = (
            conn.execute(select(consent_item).where(consent_item.c.code == "PRIVACY_REQUIRED"))
            .mappings()
            .one()
        )
    assert row["active"] is False and row["version"] == "v2"


def test_phone_is_normalized(client, engine):
    signup(client, phone="01000001234")
    with engine.connect() as conn:
        assert conn.execute(select(member.c.phone)).scalar_one() == "010-0000-1234"


# ── 로그인 ───────────────────────────────────────────────


def test_login_sets_customer_cookie(client):
    signup(client)
    client.cookies.clear()

    res = shop_login(client)

    assert res.status_code == 200
    assert res.json() == {"email": "buyer@example.com", "name": "구매자"}
    raw = res.headers["set-cookie"].lower()
    assert raw.startswith("customer_session=") and "httponly" in raw and "samesite=strict" in raw


def test_unknown_email_and_wrong_password_look_the_same(client):
    signup(client)
    wrong = shop_login(client, password=WRONG)
    unknown = shop_login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_five_failures_lock_for_fifteen_minutes(client, engine):
    signup(client)
    for _ in range(5):
        assert shop_login(client, password=WRONG).status_code == 401

    res = shop_login(client)  # 맞는 비밀번호여도 잠김
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_LOCKED"

    with engine.connect() as conn:
        locked_until, now = conn.execute(select(member.c.locked_until, func.now())).one()
    assert timedelta(minutes=14) < locked_until - now <= timedelta(minutes=15)


def test_locked_account_with_wrong_password_does_not_reveal_lock(client):
    signup(client)
    for _ in range(5):
        shop_login(client, password=WRONG)
    res = shop_login(client, password=WRONG)
    assert res.status_code == 401 and res.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_lock_expires_by_itself(client, engine):
    signup(client)
    for _ in range(5):
        shop_login(client, password=WRONG)
    with engine.begin() as conn:
        conn.execute(update(member).values(locked_until=datetime.now(UTC) - timedelta(seconds=1)))

    assert shop_login(client).status_code == 200
    with engine.connect() as conn:
        assert conn.execute(select(member.c.failed_login_count, member.c.locked_until)).one() == (
            0,
            None,
        )


def test_failures_while_locked_do_not_extend_the_lock(client, engine):
    signup(client)
    for _ in range(5):
        shop_login(client, password=WRONG)
    with engine.connect() as conn:
        before = conn.execute(select(member.c.locked_until)).scalar_one()

    for _ in range(5):
        assert shop_login(client, password=WRONG).status_code == 401

    with engine.connect() as conn:
        assert conn.execute(select(member.c.locked_until)).scalar_one() == before


# ── 고객·관리자 토큰 분리 (기능 레이어 7 결정 4) ───────────


def test_customer_token_cannot_open_admin_api(client, engine, password_hash):
    operator_id = add_operator(engine, password_hash)
    # 같은 번호의 고객 토큰을 관리자 쿠키 자리에 넣어도 통과하지 않는다
    forged = issue_token(operator_id, TEST_AUTH_SECRET.encode(), 30, CUSTOMER)
    client.cookies.set(ADMIN.cookie_name, forged)
    assert client.get("/admin/members").status_code == 401


def test_admin_token_cannot_open_shop_api(client, engine, password_hash):
    signup(client)  # 고객 1번
    client.cookies.clear()
    add_operator(engine, password_hash)  # 관리자 1번
    assert login(client).status_code == 200
    admin_token = client.cookies.get(ADMIN.cookie_name)

    client.cookies.clear()
    client.cookies.set(CUSTOMER.cookie_name, admin_token)
    assert client.get("/shop/me").status_code == 401


def test_token_without_audience_is_rejected(client):
    signup(client)
    now = datetime.now(UTC)
    no_aud = jwt.encode(
        {"sub": "1", "iat": now, "exp": now + timedelta(minutes=5)}, TEST_AUTH_SECRET, "HS256"
    )
    client.cookies.clear()
    client.cookies.set(CUSTOMER.cookie_name, no_aud)
    assert client.get("/shop/me").status_code == 401


# ── 접속기록 미대상 (CLAUDE.md 3절 #4) ─────────────────────


def test_customer_actions_are_not_access_logged(client, engine):
    signup(client)
    shop_login(client)
    client.get("/shop/me")
    client.patch("/shop/me", json={"name": "새이름"})
    client.post("/shop/auth/logout")

    assert outbox_payloads(engine) == []
