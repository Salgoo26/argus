"""주문·결제(가상 PG)·무통장입금·환불계좌 E2E (기능 레이어 7 ①, 커머스 확장 F7)

고객이 가상 PG로 구매하고 환불계좌를 등록 → 관리자(cs_kim, 상담 역할 — v0.1 보강 L-1)가
회원 상세(끝 4자리)를 보고 "전체 보기"를 누름 → 데이터 유형 PAYMENT로 기록 → relay → Argus →
결제수단 조회 룰(상) 탐지.
무통장입금: 주문(입금대기) → 관리자 입금 확인 → 고객이 환불계좌를 골라 취소 → 관리자가 이체할
계좌 전체 번호를 보고(결제수단 조회) 환불 완료.
"""

import re
import secrets

from conftest import (
    PLATFORM_URL,
    Browser,
    argus_login,
    buy,
    platform_login,
    run_detection_batch,
    shop_signup,
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
    joined, _ = shop_signup(shop, email, PASSWORD, "가상구매자", "010-0000-0003", consents)
    assert joined.status_code == 201, joined.text
    member_id = shop.get("/api/shop/me").json()["id"]

    home = {
        "label": "집",
        "recipient": "가상구매자",
        "phone": "010-0000-0003",
        "zip_code": "90003",
        "address": "가상시 가상구 가상로 3",
    }
    address_id = shop.post("/api/shop/me/addresses", json=home).json()["items"][0]["id"]
    order = buy(shop, 1, address_id)
    assert order["status"] == "PAID" and order["pg_tid"].startswith("pay_")
    saved = shop.post(
        "/api/shop/me/refund-accounts",
        json={"bank_name": "하늘은행", "account_holder": "가상구매자", "account_number": ACCOUNT},
    )
    [account] = saved.json()["items"]
    assert account["account_last4"] == "5678"

    # 무통장입금 주문 — 입금대기 (입금 계좌·기한 안내)
    deposit = shop.post(
        "/api/shop/orders",
        json={
            "product_id": 2,
            "shipping_address_id": address_id,
            "payment_method": "BANK_TRANSFER",
            "depositor_name": "가상구매자",
        },
    )
    assert deposit.status_code == 201, deposit.text
    bank_order = deposit.json()["order"]
    assert bank_order["status"] == "AWAITING_DEPOSIT" and bank_order["deposit_account_number"]

    # ── 관리자: 회원 상세(끝 4자리) → 전체 보기(결제수단) ──────────
    # 환불계좌 전체 보기는 관리자·운영·상담 역할만 (v0.1 보강 L-1) — 마케팅은 403
    reveal_path = f"/api/admin/members/{member_id}/refund-accounts/{account['id']}"
    marketing = platform_login("mkt_lee")
    assert marketing.get(reveal_path).status_code == 403
    admin = platform_login("cs_kim")
    detail = admin.get(f"/api/admin/members/{member_id}")
    assert detail.status_code == 200 and "000012345678" not in detail.text
    full = admin.get(reveal_path)
    assert full.status_code == 200 and full.json()["account_number"] == "000012345678"

    # ── 무통장입금: 입금 확인 → 고객 취소(환불계좌 선택) → 이체할 계좌 확인 → 환불 완료 ──
    confirmed = admin.post(f"/api/admin/orders/{bank_order['id']}/deposit-confirm")
    assert confirmed.status_code == 200, confirmed.text
    cancelled = shop.post(
        f"/api/shop/orders/{bank_order['id']}/cancel",
        json={"reason_code": "CHANGE_OF_MIND", "refund_account_id": account["id"]},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["claims"][0]["refund_status"] == "PENDING"
    target = admin.get(f"/api/admin/orders/{bank_order['id']}/refund-account")
    assert target.status_code == 200 and target.json()["account_number"] == "000012345678"
    done = admin.post(f"/api/admin/orders/{bank_order['id']}/refund-complete")
    assert done.status_code == 200 and done.json()["refund_status"] == "DONE"

    # ── Argus: 결제수단 조회 룰 탐지 ─────────────────────────────
    officer_browser = argus_login(*officer)

    def probe():
        run_detection_batch()
        items = officer_browser.post("/api/detections/search", json={"size": 100}).json()["items"]
        return next(
            (
                i
                for i in items
                if i["rule_name"] == "결제수단 조회" and i["actor_login_id"] == "cs_kim"
            ),
            None,
        )

    case = wait_until("cs_kim의 결제수단 조회 탐지건 생성", probe)
    assert case["severity"] == "HIGH"
    body = officer_browser.get(f"/api/detections/{case['id']}").json()
    # 원장에는 회원 PK만 — 계좌번호·이름은 Argus로 가지 않는다 (CLAUDE.md 3절 #3)
    assert "000012345678" not in str(body) and "가상구매자" not in str(body)
    subjects = [s for log in body["logs"] for s in log["subjects"]]
    assert subjects and all(MASKED.match(s) for s in subjects)
