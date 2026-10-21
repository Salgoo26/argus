"""배송관리·취소·청약철회 (커머스 확장 — 판단기록 F3·F4)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21

- 주문 상태: PAID(결제완료) → PREPARING(상품준비중) → SHIPPING(배송중) → DELIVERED(배송완료).
  고객 취소 → CANCELLED, 배송완료 후 청약철회 → RETURN_REQUESTED(반품접수) → RETURNED(반품완료)
- product.ship_days: 상품별 고정 배송 소요 일수 — 예상 도착일 = 주문일 + 소요 일수
- orders: 가상 택배사·송장번호·발송/도착 시각. 송장번호는 배송 정보라 탈퇴 시 배송 스냅샷과 함께
  분리보관으로 옮기고 비운다(shop/withdrawal.py)
- order_claim: 취소·반품(청약철회) 기록 — 사유·처리 시각·환불(카드 승인 취소) 결과.
  계약·청약철회 기록 5년(전자상거래법 제6조, 시행령 제6조제1항제1호)
- order_status_history: 상태가 바뀔 때마다 1행 — 누가(고객/관리자/시스템) 언제 바꿨는지
- 기존 주문은 PAID 그대로 둔다(시드의 배송 진행은 app/scripts/seed.py seed_fulfillment)
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE product ADD COLUMN ship_days smallint NOT NULL DEFAULT 2
    CHECK (ship_days BETWEEN 1 AND 30);
UPDATE product SET ship_days = 1 + (id % 3);

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders
    ADD CONSTRAINT orders_status_check CHECK (status IN (
        'PAID', 'PREPARING', 'SHIPPING', 'DELIVERED',
        'CANCELLED', 'RETURN_REQUESTED', 'RETURNED')),
    ADD COLUMN courier       varchar(30),
    ADD COLUMN tracking_no   varchar(30),
    ADD COLUMN shipped_at    timestamptz,
    ADD COLUMN delivered_at  timestamptz;
CREATE INDEX ix_orders_status ON orders (status, ordered_at DESC);

CREATE TABLE order_claim (
    id              bigserial     PRIMARY KEY,
    order_id        bigint        NOT NULL REFERENCES orders(id),
    claim_type      varchar(16)   NOT NULL CHECK (claim_type IN ('CANCEL', 'RETURN')),
    reason_code     varchar(32)   NOT NULL,
    reason_detail   varchar(500),
    status          varchar(16)   NOT NULL CHECK (status IN ('REQUESTED', 'COMPLETED')),
    requested_at    timestamptz   NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    completed_by    bigint        REFERENCES operator(id),
    refund_amount   int,
    refund_pg_tid   varchar(64)
);
CREATE INDEX ix_order_claim_order ON order_claim (order_id, id);

CREATE TABLE order_status_history (
    id            bigserial     PRIMARY KEY,
    order_id      bigint        NOT NULL REFERENCES orders(id),
    from_status   varchar(16),
    to_status     varchar(16)   NOT NULL,
    actor_type    varchar(16)   NOT NULL CHECK (actor_type IN ('CUSTOMER', 'OPERATOR', 'SYSTEM')),
    operator_id   bigint        REFERENCES operator(id),
    changed_at    timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX ix_order_status_history_order ON order_status_history (order_id, id);
"""

DOWNGRADE_SQL = """
DROP TABLE order_status_history;
DROP TABLE order_claim;
DROP INDEX ix_orders_status;
ALTER TABLE orders
    DROP CONSTRAINT orders_status_check,
    DROP COLUMN delivered_at,
    DROP COLUMN shipped_at,
    DROP COLUMN tracking_no,
    DROP COLUMN courier;
UPDATE orders SET status = 'PAID';
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN ('PAID'));
ALTER TABLE product DROP COLUMN ship_days;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
