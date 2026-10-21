"""외부 수탁자 연동 — 가상 PG(결제대행)·가상 택배 (커머스 확장 판단기록 F6)

두 서비스는 쇼핑몰과 분리된 별도 시스템이다(자체 DB·자체 키, apps/mock-pg·apps/mock-courier).
쇼핑몰이 보내는 개인정보:
- PG: 결제창을 열 때 구매자 이름·이메일·휴대폰 (F6 Q1) — 브라우저가 PG 결제창으로 POST
- 택배: 받는 사람 이름·연락처·주소, 배송 메모, 상품명·수량, 주문번호, 보내는 곳(쇼핑몰) (F6 Q2)
받는 것:
- PG: 승인 결과(카드사·마스킹 카드번호·승인번호·할부·영수증 주소), 취소 거래번호
- 택배: 접수 번호, 상태 통지(집하·이동·배송완료, 송장번호) — app/partners/courier_events.py
"""

from dataclasses import dataclass

from app.partners.courier import CourierClient
from app.partners.pg import PgClient

# 보내는 곳(쇼핑몰) — 가상 상호·주소
SHOP_SENDER = {
    "name": "데모샵(가상)",
    "phone": "02-0000-0000",
    "zipCode": "90000",
    "address": "서울특별시 가상구 물류로 1",
    "addressDetail": "데모샵 출고센터",
}


@dataclass
class Partners:
    pg: PgClient
    courier: CourierClient


def pg_order_id(order_id: int) -> str:
    """PG·택배에 보내는 주문번호 (주문 PK 기반)"""
    return f"ORDER-{order_id:08d}"
