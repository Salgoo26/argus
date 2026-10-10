"""고객 화면 최소판 E2E — 가입(동의) → 마이페이지 → 동의 철회 → 탈퇴(즉시 파기)

화면 서버(Next.js) 경유로 브라우저와 같은 경로를 쓴다. 고객 행위는 접속기록 대상이 아니므로
(CLAUDE.md 3절 #4) Argus 쪽 확인은 하지 않고, 고객 쿠키로 관리자 API가 열리지 않는지만 본다.
"""

import secrets

from conftest import PLATFORM_URL, Browser

PASSWORD = "e2e-buyer-pass-1"  # noqa: S105 — 테스트 전용 더미 값, 매번 새 가상 계정
REQUIRED = {"TOS": True, "AGE_OVER_14": True}


def test_customer_signup_mypage_withdraw():
    email = f"e2e-{secrets.token_hex(4)}@example.com"
    shop = Browser(PLATFORM_URL, "customer_session")

    items = shop.get("/api/shop/consent-items").json()
    assert {i["code"] for i in items} >= set(REQUIRED) | {"MARKETING"}
    # 계약 이행 항목은 동의 대신 안내 (§15①4·§22③) — 개인정보 수집·이용 (필수) 동의는 받지 않는다
    assert "PRIVACY_REQUIRED" not in {i["code"] for i in items}

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
    assert "address" not in edited.json()  # 주소는 회원 정보가 아니라 배송지

    # 배송지: 첫 배송지는 기본, 두 번째를 기본으로 바꾸면 기본은 하나
    home = {
        "label": "집",
        "recipient": "가상고객",
        "phone": "010-0000-0001",
        "zip_code": "90001",
        "address": "가상시 가상구 가상로 1",
    }
    added = shop.post("/api/shop/me/addresses", json=home)
    assert added.status_code == 201, added.text
    office = shop.post("/api/shop/me/addresses", json={**home, "label": "회사"}).json()["items"]
    office_id = next(a["id"] for a in office if a["label"] == "회사")
    items = shop.post(f"/api/shop/me/addresses/{office_id}/default").json()["items"]
    assert [a["label"] for a in items if a["is_default"]] == ["회사"]

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
