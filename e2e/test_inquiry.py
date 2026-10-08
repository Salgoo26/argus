"""1:1 문의 E2E (기능 레이어 7 ②, actor-flows F-01 #5 → F-02)

고객이 문의 → CS(cs_choi)가 문의 상세(티켓 확보) → 회원 상세 → 답변 → 고객이 답변 확인.
CS의 문의 처리 기록에 실린 티켓 번호(INQ-번호)가 relay → 수집 API를 거쳐 Argus 원장에 도착했는지
담당자 접속기록 검색으로 확인한다 — 형식 오류로 DEAD가 되면 조용히 사라지기 때문.
"""

import secrets

from conftest import PLATFORM_URL, Browser, argus_login, platform_login, wait_until

PASSWORD = "e2e-buyer-pass-3"  # noqa: S105 — 테스트 전용 더미 값, 매번 새 가상 계정
BODY = "배송이 언제 되나요? (E2E 가상 문의)"


def test_inquiry_ticket_reaches_argus(officer):
    shop = Browser(PLATFORM_URL, "customer_session")
    consents = {"TOS": True, "PRIVACY_REQUIRED": True, "AGE_OVER_14": True}
    email = f"e2e-{secrets.token_hex(4)}@example.com"
    assert (
        shop.post(
            "/api/shop/auth/signup",
            json={
                "email": email,
                "password": PASSWORD,
                "name": "가상문의자",
                "phone": "010-0000-0002",  # 가상
                "consents": consents,
            },
        ).status_code
        == 201
    )
    asked = shop.post("/api/shop/inquiries", json={"title": "배송 문의", "body": BODY})
    assert asked.status_code == 201
    inquiry_id = asked.json()["id"]
    ticket = f"INQ-{inquiry_id}"

    cs = platform_login("cs_choi")
    detail = cs.get(f"/api/admin/inquiries/{inquiry_id}")
    assert detail.status_code == 200 and detail.json()["ticket_id"] == ticket
    member_id = detail.json()["member_id"]
    assert cs.get(f"/api/admin/members/{member_id}").status_code == 200
    answered = cs.post(f"/api/admin/inquiries/{inquiry_id}/answer", json={"answer": "내일 도착"})
    assert answered.status_code == 200

    mine = shop.get("/api/shop/inquiries").json()["items"]
    assert mine[0]["answer"] == "내일 도착"

    # Argus 원장: cs_choi의 문의 상세·답변 기록이 회원번호와 함께 도착 (내용은 안 감)
    officer_browser = argus_login(*officer)

    def probe():
        res = officer_browser.post(
            "/api/access-logs/search",
            json={"actor": "cs_choi", "subject": str(member_id), "size": 100},
        )
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        kinds = {(i["action"], i["data_category"]) for i in items}
        wanted = {("READ", "INQUIRY"), ("UPDATE", "INQUIRY"), ("READ", "MEMBER_BASIC")}
        return items if wanted <= kinds else None

    items = wait_until("cs_choi의 문의 처리 기록 원장 도착", probe)
    assert BODY not in str(items) and "가상문의자" not in str(items)
