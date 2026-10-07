"""데이터 유형 분류표 — 플랫폼의 개인정보 테이블이 빠짐없이 등록돼 있는지 (architecture 3-4)

분류표에 없는 테이블은 NONE이 되고, DB 직접 접근 기본 룰은 개인정보 유형만 보므로 탐지에서 빠진다.
플랫폼에 개인정보 테이블을 추가하면 이 목록과 app/sql.py의 TABLE_CATEGORY를 함께 고친다.
"""

import pytest

from app.sql import TABLE_CATEGORY, analyze

# 플랫폼 DB의 개인정보·거래 테이블 (apps/platform-api/migrations) → 기대 유형
PLATFORM_PERSONAL_TABLES = {
    "member": "MEMBER_BASIC",
    "member_consent": "MEMBER_BASIC",
    "retained_member_record": "MEMBER_BASIC",
    "shipping_address": "MEMBER_BASIC",  # 0007 배송지 (2026-10-07)
    "refund_account": "PAYMENT",
    "inquiry": "INQUIRY",
    "orders": "ORDER",
    "payment": "ORDER",
}


def test_every_platform_personal_table_is_classified():
    assert TABLE_CATEGORY == PLATFORM_PERSONAL_TABLES


@pytest.mark.parametrize(
    ("sql", "category"),
    [
        ("SELECT recipient, address FROM shipping_address WHERE member_id = 10001", "MEMBER_BASIC"),
        ("UPDATE shipping_address SET phone = '010-0000-0001' WHERE id = 1", "MEMBER_BASIC"),
        ("DELETE FROM public.shipping_address WHERE id = 1", "MEMBER_BASIC"),
        # 주문과 같이 보면 더 민감한 쪽
        (
            "SELECT o.id, s.address FROM orders o JOIN shipping_address s"
            " ON s.member_id = o.member_id",
            "MEMBER_BASIC",
        ),
    ],
)
def test_shipping_address_is_member_basic(sql, category):
    result = analyze(sql)
    assert result.data_category == category
    assert any(t.endswith("shipping_address") for t in result.tables)
    assert "010-0000-0001" not in result.normalized and "10001" not in result.normalized
