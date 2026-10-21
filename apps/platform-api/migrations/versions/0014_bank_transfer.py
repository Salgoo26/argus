"""무통장입금·환불계좌 여러 개 (커머스 확장 — 판단기록 F7)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-21

- 주문 상태 추가: AWAITING_DEPOSIT(입금대기), DEPOSIT_EXPIRED(입금 기한 경과).
  은행 연동 없이 관리자가 입금을 확인하면 PAID가 된다
- bank_deposit: 주문 1건당 1행 — 주문 시점의 쇼핑몰 입금 계좌·입금자명·입금 기한·입금 확인(처리자)
- payment: 무통장입금 결제(method BANK_TRANSFER)는 입금 확인 때 1행.
  카드사·PG 거래번호는 카드에만 필수
- refund_account: 회원당 1개 → 여러 개(배송지처럼 마이페이지에서 관리,
  환불할 때 목록에서 고른다 — F7-1)
- order_claim: 환불 방식(PG 취소/계좌 이체)·환불 처리 상태, 이체 환불 계좌(고른 시점 사본 —
  계좌번호는 AES-256-GCM 암호문, §7②6호)와 이체 완료 처리(처리자)
- 기존 취소·반품 기록은 PG 취소로 처리 완료된 것으로 채운다
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN (
    'PENDING_PAYMENT', 'PAYMENT_EXPIRED', 'AWAITING_DEPOSIT', 'DEPOSIT_EXPIRED',
    'PAID', 'PREPARING', 'SHIPPING', 'DELIVERED',
    'CANCELLED', 'RETURN_REQUESTED', 'RETURNED'));

CREATE TABLE bank_deposit (
    order_id        bigint       PRIMARY KEY REFERENCES orders(id),
    bank_name       varchar(50)  NOT NULL,   -- 쇼핑몰 입금 계좌 (주문 시점 사본)
    account_number  varchar(30)  NOT NULL,
    account_holder  varchar(50)  NOT NULL,
    depositor_name  varchar(50)  NOT NULL,   -- 고객이 입력한 입금자명 — 입금 확인 대조용
    due_at          timestamptz  NOT NULL,
    confirmed_at    timestamptz,
    confirmed_by    bigint       REFERENCES operator(id)
);

ALTER TABLE payment DROP CONSTRAINT payment_method_check;
ALTER TABLE payment
    ALTER COLUMN card_company DROP NOT NULL,
    ALTER COLUMN pg_tid DROP NOT NULL,
    ADD CONSTRAINT payment_method_check CHECK (method IN ('CARD', 'BANK_TRANSFER')),
    ADD CONSTRAINT payment_card_fields CHECK (
        method <> 'CARD' OR (card_company IS NOT NULL AND pg_tid IS NOT NULL));

ALTER TABLE refund_account DROP CONSTRAINT refund_account_member_id_key;
CREATE INDEX ix_refund_account_member ON refund_account (member_id, id);

ALTER TABLE order_claim
    ADD COLUMN refund_method          varchar(16)
        CHECK (refund_method IN ('PG_CANCEL', 'BANK_TRANSFER')),
    ADD COLUMN refund_status          varchar(16) CHECK (refund_status IN ('PENDING', 'DONE')),
    ADD COLUMN refund_bank_name       varchar(50),
    ADD COLUMN refund_account_holder  varchar(50),
    ADD COLUMN refund_account_enc     bytea,
    ADD COLUMN refund_account_last4   char(4),
    ADD COLUMN refunded_at            timestamptz,
    ADD COLUMN refunded_by            bigint REFERENCES operator(id);
UPDATE order_claim
    SET refund_method = 'PG_CANCEL', refund_status = 'DONE', refunded_at = completed_at
    WHERE refund_pg_tid IS NOT NULL;
"""

DOWNGRADE_SQL = """
ALTER TABLE order_claim
    DROP COLUMN refunded_by,
    DROP COLUMN refunded_at,
    DROP COLUMN refund_account_last4,
    DROP COLUMN refund_account_enc,
    DROP COLUMN refund_account_holder,
    DROP COLUMN refund_bank_name,
    DROP COLUMN refund_status,
    DROP COLUMN refund_method;

DROP INDEX ix_refund_account_member;
DELETE FROM refund_account a USING refund_account b
    WHERE a.member_id = b.member_id AND a.id < b.id;
ALTER TABLE refund_account ADD CONSTRAINT refund_account_member_id_key UNIQUE (member_id);

DELETE FROM order_status_history WHERE order_id IN (
    SELECT o.id FROM orders o JOIN bank_deposit d ON d.order_id = o.id);
DELETE FROM order_claim WHERE order_id IN (SELECT order_id FROM bank_deposit);
DELETE FROM payment WHERE method = 'BANK_TRANSFER';
ALTER TABLE payment
    DROP CONSTRAINT payment_card_fields,
    DROP CONSTRAINT payment_method_check,
    ALTER COLUMN pg_tid SET NOT NULL,
    ALTER COLUMN card_company SET NOT NULL,
    ADD CONSTRAINT payment_method_check CHECK (method IN ('CARD'));
CREATE TEMP TABLE _bank_orders AS SELECT order_id FROM bank_deposit;
DROP TABLE bank_deposit;
DELETE FROM orders WHERE id IN (SELECT order_id FROM _bank_orders);

ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN (
    'PENDING_PAYMENT', 'PAYMENT_EXPIRED',
    'PAID', 'PREPARING', 'SHIPPING', 'DELIVERED',
    'CANCELLED', 'RETURN_REQUESTED', 'RETURNED'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
