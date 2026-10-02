"""1:1 문의 — 고객 작성, CS 처리 접속기록(ticket_id), 탈퇴 시 분쟁 기록 분리보관 (②)"""

import pytest
from sqlalchemy import func, select

from app.agent import record_context
from app.models import inquiry, retained_member_record

from conftest import CUSTOMER_PASSWORD, add_operator, login, outbox_payloads, signup

BODY = "주문한 상품이 언제 도착하나요?"


def _ask(client, title: str = "배송 문의", body: str = BODY):
    return client.post("/shop/inquiries", json={"title": title, "body": body})


@pytest.fixture
def cs_client(client, engine, password_hash):
    """고객 1명이 문의 2건을 남긴 뒤 CS(cs_kim)로 로그인한 client"""
    signup(client)
    _ask(client)
    _ask(client, "환불 요청", "환불해 주세요.")
    client.cookies.clear()
    add_operator(engine, password_hash, login_id="cs_kim", name="김민지", team="CS", role="CS")
    assert login(client, "cs_kim").status_code == 200
    return client


def _events(engine) -> list[dict]:
    return [p for p in outbox_payloads(engine) if p.get("data_category") == "INQUIRY"]


# ── 고객 ─────────────────────────────────────────────────


def test_customer_asks_and_sees_only_own_inquiries(client):
    signup(client)
    res = _ask(client)
    assert res.status_code == 201 and res.json()["status"] == "OPEN"
    signup(client, email="other@example.com")
    _ask(client, "다른 사람 문의")

    titles = [i["title"] for i in client.get("/shop/inquiries").json()["items"]]
    assert titles == ["다른 사람 문의"]


@pytest.mark.parametrize("body", [{"title": " ", "body": BODY}, {"title": "t", "body": ""}])
def test_blank_inquiry_is_rejected(client, body):
    signup(client)
    assert client.post("/shop/inquiries", json=body).status_code == 400


def test_inquiry_requires_customer_login(client):
    assert _ask(client).status_code == 401


def test_customer_inquiry_is_not_access_logged(client, engine):
    signup(client)
    _ask(client)
    client.get("/shop/inquiries")
    assert outbox_payloads(engine) == []


# ── CS 처리 (F-02) ───────────────────────────────────────


def test_list_hides_body_and_records_authors(cs_client, engine):
    res = cs_client.get("/admin/inquiries", params={"status": "OPEN"})

    assert res.status_code == 200 and res.json()["total"] == 2
    assert BODY not in res.text  # 목록엔 본문이 없다
    [event] = _events(engine)
    assert event["action"] == "READ" and event["subject"]["ids"] == ["1"]  # 같은 작성자 → 1명
    assert event["context"] == {}


def test_detail_records_ticket_id(cs_client, engine):
    res = cs_client.get("/admin/inquiries/1")

    assert res.status_code == 200
    body = res.json()
    assert body["body"] == BODY and body["ticket_id"] == "INQ-1" and body["member_id"] == 1
    [event] = _events(engine)
    assert event["context"] == {"ticket_id": "INQ-1"}  # api-spec 2-4 "문의 상세 확인"
    assert event["subject"]["ids"] == ["1"]
    assert BODY not in str(event)  # 문의 내용은 Argus로 가지 않는다 (CLAUDE.md 3절 #3)


def test_answer_once_and_record_update(cs_client, engine):
    res = cs_client.post("/admin/inquiries/1/answer", json={"answer": "내일 도착합니다."})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ANSWERED" and body["answered_by_login_id"] == "cs_kim"
    again = cs_client.post("/admin/inquiries/1/answer", json={"answer": "다시"})
    assert again.status_code == 409

    updates = [e for e in _events(engine) if e["action"] == "UPDATE"]
    assert [e["result"] for e in updates] == ["SUCCESS", "FAILURE"]
    # 실패한 답변도 누구의 문의였는지·어느 티켓인지 남는다
    assert all(
        e["subject"]["ids"] == ["1"] and e["context"] == {"ticket_id": "INQ-1"} for e in updates
    )


def test_customer_sees_answer(cs_client):
    cs_client.post("/admin/inquiries/2/answer", json={"answer": "환불했습니다."})
    cs_client.cookies.clear()
    from conftest import shop_login

    shop_login(cs_client)
    items = {i["id"]: i for i in cs_client.get("/shop/inquiries").json()["items"]}
    assert items[2]["answer"] == "환불했습니다." and "answered_by" not in items[2]


def test_missing_inquiry_still_records_ticket(cs_client, engine):
    assert cs_client.get("/admin/inquiries/99").status_code == 404
    [event] = _events(engine)
    assert event["result"] == "FAILURE" and event["context"] == {"ticket_id": "INQ-99"}


def test_unknown_context_key_is_refused_before_sending():
    # Argus는 정의되지 않은 키를 이벤트째 거부한다 — 플랫폼에서 먼저 막는다
    with pytest.raises(ValueError):
        record_context(member_name="홍길동")


# ── 탈퇴: 문의 내용 분리보관 3년 ─────────────────────────


def test_withdraw_moves_inquiry_content_to_retention(client, engine):
    signup(client)
    _ask(client)

    assert client.post("/shop/me/withdraw", json={"password": CUSTOMER_PASSWORD}).status_code == 204

    with engine.connect() as conn:
        row = conn.execute(select(inquiry)).mappings().one()
        retained = (
            conn.execute(
                select(retained_member_record).where(
                    retained_member_record.c.retain_reason == "DISPUTE_3Y"
                )
            )
            .mappings()
            .one()
        )
        orders_retained = conn.execute(
            select(func.count())
            .select_from(retained_member_record)
            .where(retained_member_record.c.retain_reason == "PAYMENT_5Y")
        ).scalar_one()

    # 운영 테이블: 회원 연결이 끊기고 내용도 비워짐 (번호·상태·시각만)
    assert row["member_id"] is None and BODY not in row["body"] and row["answer"] is None
    assert retained["legal_basis"] == "전자상거래법 시행령 §6①4호"
    assert retained["data"]["inquiries"][0]["body"] == BODY
    years = (retained["retain_until"] - retained["created_at"]).days / 365
    assert 2.99 < years < 3.01
    assert orders_retained == 0  # 주문이 없으면 결제 기록 보관도 없음
