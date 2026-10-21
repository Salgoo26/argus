"""가상 PG·가상 택배 연동 (커머스 확장 — 판단기록 F6)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21

- 주문 상태 추가: PENDING_PAYMENT(결제대기 — PG 결제창을 연 상태),
  PAYMENT_EXPIRED(결제 미완료로 만료). 주문을 먼저 결제대기로 만들고,
  PG 승인 API가 성공하면 PAID로 바꾼다
- orders.delivery_memo: 배송 메모(주문할 때 입력) — 택배 접수 때 함께 보낸다
- orders.courier_shipment_id·courier_status·courier_synced_at: 택배 접수 번호와
  택배사가 알려 준 최근 상태. 송장번호(tracking_no)는 택배사가 집하할 때 발급해 통지로 받는다
- order_claim.pickup_*: 반품 회수 접수 번호·상태·회수 송장번호
- payment: PG 승인 응답의 마스킹 카드번호(앞 6·뒤 4자리)·승인번호·할부 개월·영수증 주소
  (판단기록 F6 Q1)
- 기존 주문·결제는 그대로 둔다 — 연동 이전 데이터(PG·택배 서비스에는 기록이 없음)
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders
    ADD CONSTRAINT orders_status_check CHECK (status IN (
        'PENDING_PAYMENT', 'PAYMENT_EXPIRED',
        'PAID', 'PREPARING', 'SHIPPING', 'DELIVERED',
        'CANCELLED', 'RETURN_REQUESTED', 'RETURNED')),
    ADD COLUMN delivery_memo        varchar(50),
    ADD COLUMN courier_shipment_id  varchar(32) UNIQUE,
    ADD COLUMN courier_status       varchar(20),
    ADD COLUMN courier_synced_at    timestamptz;

ALTER TABLE order_claim
    ADD COLUMN pickup_shipment_id   varchar(32) UNIQUE,
    ADD COLUMN pickup_status        varchar(20),
    ADD COLUMN pickup_tracking_no   varchar(30);

ALTER TABLE payment
    ADD COLUMN card_number_masked   varchar(20),
    ADD COLUMN approve_no           varchar(12),
    ADD COLUMN installment_months   smallint,
    ADD COLUMN receipt_url          varchar(255);
"""

DOWNGRADE_SQL = """
ALTER TABLE payment
    DROP COLUMN receipt_url,
    DROP COLUMN installment_months,
    DROP COLUMN approve_no,
    DROP COLUMN card_number_masked;
ALTER TABLE order_claim
    DROP COLUMN pickup_tracking_no,
    DROP COLUMN pickup_status,
    DROP COLUMN pickup_shipment_id;
DELETE FROM order_status_history
    WHERE order_id IN (
        SELECT id FROM orders WHERE status IN ('PENDING_PAYMENT', 'PAYMENT_EXPIRED'));
DELETE FROM orders WHERE status IN ('PENDING_PAYMENT', 'PAYMENT_EXPIRED');
ALTER TABLE orders
    DROP CONSTRAINT orders_status_check,
    DROP COLUMN courier_synced_at,
    DROP COLUMN courier_status,
    DROP COLUMN courier_shipment_id,
    DROP COLUMN delivery_memo;
ALTER TABLE orders
    ADD CONSTRAINT orders_status_check CHECK (status IN (
        'PAID', 'PREPARING', 'SHIPPING', 'DELIVERED',
        'CANCELLED', 'RETURN_REQUESTED', 'RETURNED'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
