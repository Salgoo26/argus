"""주문·결제(PG 목업)·환불계좌 E2E (기능 레이어 7 ①)

고객이 가상 PG로 구매하고 환불계좌를 등록 → 관리자(mkt_lee)가 회원 상세(끝 4자리)를 보고
"전체 보기"를 누름 → 데이터 유형 PAYMENT로 기록 → relay → Argus → 결제수단 조회 룰(상) 탐지.
"""

import re
import secrets

from conftest import (
    PLATFORM_URL,
    Browser,
    argus_login,
    platform_login,
    run_detection_batch,
    wait_until,
)

PASSWORD = "e2e-buyer-pass-2"  # noqa: S105 — 테스트 전용 더미 값, 매번 새 가상 계정
ACCOUNT = "0000-1234-5678"  # 가상 계좌번호
MASKED = re.compile(r"^member_\d+\*\*\*$")


def test_payment_full_view_is_detected(officer):
    # ── 고객: 가입 → 구매(가상 PG) → 환불계좌 등록 ─────────────
    shop = Browser(PLATFORM_URL, "customer_session")
    email = f"e2e-{secrets.token_hex(4)}@example.com"
    consents = {"TOS": True, "PRIVACY_REQUIRED": True, "AGE_OVER_14": True}
    joined = shop.post(
        "/api/shop/auth/signup",
        json={"email": email, "password": PASSWORD, "name": "가상구매자", "consents": consents},
    )
    assert joined.status_code == 201, joined.text
    member_id = shop.get("/api/shop/me").json()["id"]

    order = shop.post("/api/shop/orders", json={"product_id": 1, "card_company": "하늘카드"})
    assert order.status_code == 201 and order.json()["pg_tid"].startswith("MOCKPG-")
    saved = shop.request(
        "PUT",
        "/api/shop/me/refund-account",
        json={"bank_name": "하늘은행", "account_holder": "가상구매자", "account_number": ACCOUNT},
    )
    assert saved.json()["refund_account"]["account_last4"] == "5678"

    # ── 관리자: 회원 상세(끝 4자리) → 전체 보기(결제수단) ──────────
    admin = platform_login("mkt_lee")
    detail = admin.get(f"/api/admin/members/{member_id}")
    assert detail.status_code == 200 and "000012345678" not in detail.text
    full = admin.get(f"/api/admin/members/{member_id}/refund-account")
    assert full.status_code == 200 and full.json()["account_number"] == "000012345678"

    # ── Argus: 결제수단 조회 룰 탐지 ─────────────────────────────
    officer_browser = argus_login(*officer)

    def probe():
        run_detection_batch()
        items = officer_browser.get("/api/detections", params={"size": 100}).json()["items"]
        return next(
            (
                i
                for i in items
                if i["rule_name"] == "결제수단 조회" and i["actor_login_id"] == "mkt_lee"
            ),
            None,
        )

    case = wait_until("mkt_lee의 결제수단 조회 탐지건 생성", probe)
    assert case["severity"] == "HIGH"
    body = officer_browser.get(f"/api/detections/{case['id']}").json()
    # 원장에는 회원 PK만 — 계좌번호·이름은 Argus로 가지 않는다 (CLAUDE.md 3절 #3)
    assert "000012345678" not in str(body) and "가상구매자" not in str(body)
    subjects = [s for log in body["logs"] for s in log["subjects"]]
    assert subjects and all(MASKED.match(s) for s in subjects)
