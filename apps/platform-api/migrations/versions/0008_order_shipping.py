"""주문의 배송 정보 스냅샷 (기능 레이어 7-4 ③, db-schema 4절 "플랫폼 보강", PLT-04)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07

주문할 때 고른 배송지의 받는 사람·연락처·우편번호·주소를 주문 행에 **복사**한다 — 배송지를 고치거나
지워도 "그 주문을 어디로 보냈는지"는 바뀌지 않아야 하기 때문(재화 공급 기록).
배송지를 FK로 가리키지 않는 이유도 같다.

- 모두 NULL 허용: 이 기능 이전의 주문에는 배송 정보가 없다(지어 넣지 않는다). 탈퇴 시에는
  PAYMENT_5Y 분리보관으로 옮긴 뒤 비운다(shop/withdrawal.py, db-schema 4-1)
- 게이트웨이 데이터 유형은 그대로 ORDER (architecture 3-4 — orders → ORDER)
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE orders
    ADD COLUMN ship_recipient       varchar(50),
    ADD COLUMN ship_phone           varchar(20),
    ADD COLUMN ship_zip_code        varchar(5),
    ADD COLUMN ship_address         varchar(255),
    ADD COLUMN ship_address_detail  varchar(100);
"""

DOWNGRADE_SQL = """
ALTER TABLE orders
    DROP COLUMN ship_address_detail,
    DROP COLUMN ship_address,
    DROP COLUMN ship_zip_code,
    DROP COLUMN ship_phone,
    DROP COLUMN ship_recipient;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
