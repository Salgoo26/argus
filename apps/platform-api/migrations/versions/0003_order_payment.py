"""주문·결제(PG 목업)·환불계좌 + 탈퇴 시 분리보관·파기 이력 (기능 레이어 7 ①, 결정 6·7)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02

db-schema 4절과 다른 점 (설계 변경 — 구현 로그):
- payment_method(카드번호·계좌번호 암호화) 대신 두 테이블로 나눈다
  - payment: PG 결제 결과 — **카드번호는 받지도 저장하지도 않는다(끝 4자리도)**. 결제수단 종류·
    카드사·PG 거래번호·승인 시각·금액만. 카드번호는 PG 결제창에서 PG사가 받는다(목업)
  - refund_account: 고객이 마이페이지에서 등록하는 환불계좌 — 계좌번호는 AES-256-GCM 암호문,
    화면 표시용 끝 4자리는 평문
- orders: 설계 그대로(상품 1개·수량 1). 상태는 지금 결제 완료(PAID)만
- retained_member_record·destruction_history: 설계 그대로 — 탈퇴 즉시 파기(PR B)에 주문 기록
  분리보관을 붙인다
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE product (
    id     bigserial    PRIMARY KEY,
    name   varchar(100) NOT NULL,
    price  int          NOT NULL CHECK (price > 0)
);

CREATE TABLE orders (
    id          bigserial   PRIMARY KEY,
    member_id   bigint      REFERENCES member(id) ON DELETE SET NULL,  -- 탈퇴 시 끊긴다
    product_id  bigint      NOT NULL REFERENCES product(id),
    amount      int         NOT NULL CHECK (amount > 0),
    status      varchar(16) NOT NULL CHECK (status IN ('PAID')),
    ordered_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_orders_member ON orders (member_id, ordered_at DESC);

-- PG 결제 결과 — 카드번호 컬럼이 없다 (최소수집: 카드 등록·간편결제 기능이 없어 쓸 목적이 없음)
CREATE TABLE payment (
    id            bigserial   PRIMARY KEY,
    order_id      bigint      NOT NULL UNIQUE REFERENCES orders(id),
    method        varchar(16) NOT NULL CHECK (method IN ('CARD')),
    card_company  varchar(30) NOT NULL,
    pg_tid        varchar(64) NOT NULL UNIQUE,      -- PG 거래번호 (환불·분쟁 시 PG사 조회 키)
    amount        int         NOT NULL CHECK (amount > 0),
    approved_at   timestamptz NOT NULL
);

-- 환불계좌 (§7②6호 계좌번호 암호화 대상) — 회원당 1개
CREATE TABLE refund_account (
    id                  bigserial   PRIMARY KEY,
    member_id           bigint      NOT NULL UNIQUE REFERENCES member(id),
    bank_name           varchar(50) NOT NULL,
    account_holder      varchar(50) NOT NULL,
    account_number_enc  bytea       NOT NULL,   -- AES-256-GCM (키는 .env, DB와 분리)
    account_last4       char(4)     NOT NULL,   -- 화면 표시용
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- 법정 보존 항목 분리보관 (PIPA §21③)
CREATE TABLE retained_member_record (
    id                  bigserial    PRIMARY KEY,
    original_member_id  bigint       NOT NULL,          -- FK 아님 (원본 member 행은 파기됨)
    retain_reason       varchar(64)  NOT NULL,          -- 'PAYMENT_5Y','DISPUTE_3Y'
    legal_basis         varchar(100) NOT NULL,          -- '전자상거래법 시행령 §6'
    data                jsonb        NOT NULL,          -- 보존 목적에 필요한 최소 항목만
    retain_until        timestamptz  NOT NULL,
    created_at          timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_retained_until ON retained_member_record (retain_until);

-- 파기 이력 (개인정보 자체는 기록하지 않는다)
CREATE TABLE destruction_history (
    id             bigserial    PRIMARY KEY,
    executed_at    timestamptz  NOT NULL DEFAULT now(),
    target_type    varchar(32)  NOT NULL,     -- 'MEMBER','ACCESS_LOG_OUTBOX' 등
    cutoff_at      timestamptz  NOT NULL,
    deleted_count  int          NOT NULL,
    legal_basis    varchar(100) NOT NULL
);

INSERT INTO product (name, price) VALUES
  ('무지 머그컵', 12000),
  ('리넨 에코백', 18000),
  ('스테인리스 텀블러', 24000),
  ('면 반팔 티셔츠', 19000),
  ('가죽 카드지갑', 35000),
  ('무선 마우스', 29000);
"""

DOWNGRADE_SQL = """
DROP TABLE destruction_history;
DROP TABLE retained_member_record;
DROP TABLE refund_account;
DROP TABLE payment;
DROP TABLE orders;
DROP TABLE product;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
