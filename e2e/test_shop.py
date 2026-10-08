"""고객 화면 최소판 E2E — 가입(동의) → 마이페이지 → 동의 철회 → 탈퇴(즉시 파기)

화면 서버(Next.js) 경유로 브라우저와 같은 경로를 쓴다. 고객 행위는 접속기록 대상이 아니므로
(CLAUDE.md 3절 #4) Argus 쪽 확인은 하지 않고, 고객 쿠키로 관리자 API가 열리지 않는지만 본다.
"""

import secrets

from conftest import PLATFORM_URL, Browser

PASSWORD = "e2e-buyer-pass-1"  # noqa: S105 — 테스트 전용 더미 값, 매번 새 가상 계정
REQUIRED = {"TOS": True, "PRIVACY_REQUIRED": True, "AGE_OVER_14": True}


def test_customer_signup_mypage_withdraw():
    email = f"e2e-{secrets.token_hex(4)}@example.com"
    shop = Browser(PLATFORM_URL, "customer_session")

    items = shop.get("/api/shop/consent-items").json()
    assert {i["code"] for i in items} >= set(REQUIRED) | {"MARKETING"}

    profile = {"email": email, "password": PASSWORD, "name": "가상고객", "phone": "01000000001"}

    # 필수 동의가 빠지면 가입 불가
    refused = shop.post("/api/shop/auth/signup", json={**profile, "consents": {}})
    assert refused.status_code == 400
    # 휴대폰은 가입 필수 (2026-10-07 플랫폼 보강)
    no_phone = {k: v for k, v in profile.items() if k != "phone"}
    refused = shop.post("/api/shop/auth/signup", json={**no_phone, "consents": REQUIRED})
    assert refused.status_code == 400

    joined = shop.post("/api/shop/auth/signup", json={**profile, "consents": REQUIRED})
    assert joined.status_code == 201, joined.text

    me = shop.get("/api/shop/me").json()
    assert {c["code"]: c["agreed"] for c in me["consents"]}["MARKETING"] is False
    assert me["phone"] == "010-0000-0001"  # 하이픈 형식으로 저장

    # 정정: 이름·휴대폰은 바뀌고, 이메일(로그인 아이디)은 보내도 바뀌지 않는다
    edited = shop.request(
        "PATCH",
        "/api/shop/me",
        json={"name": "가상고객2", "phone": "010-0000-0009", "email": "other@example.com"},
    )
    assert edited.status_code == 200
    assert (edited.json()["name"], edited.json()["email"]) == ("가상고객2", email)

    # 고객 세션 토큰으로는 관리자 API가 열리지 않는다 (토큰 용도 분리)
    admin_try = Browser(PLATFORM_URL, "platform_session")
    admin_try.token = shop.token
    assert admin_try.get("/api/admin/members").status_code == 401

    agreed = shop.request("PUT", "/api/shop/me/consents/MARKETING", json={"agreed": True})
    assert agreed.status_code == 200

    gone = shop.post("/api/shop/me/withdraw", json={"password": PASSWORD})
    assert gone.status_code == 204
    again = Browser(PLATFORM_URL, "customer_session")
    login = again.post("/api/shop/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 401  # 계정 자체가 파기됨
