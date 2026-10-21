"""가상 PG 자체 DB — 쇼핑몰(플랫폼) DB와 완전히 분리된 별도 인스턴스

스키마는 기동할 때 만든다(없을 때만). 작은 외부 서비스 흉내라 마이그레이션 도구를 두지 않는다.

결제 상태 (토스페이먼츠 등 실제 PG의 흐름을 단순화):
    READY ─(결제창 카드 인증)─▶ IN_PROGRESS ─(쇼핑몰 서버 승인 API)─▶ DONE ─(취소 API)─▶ CANCELED
      └─(결제창에서 취소)─▶ ABORTED   └─(제한 시간 초과)─▶ EXPIRED

카드번호 전체는 저장하지 않는다 — 앞 6자리·뒤 4자리만 남긴 마스킹 값만 둔다.
"""

from sqlalchemy import Engine, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment (
    payment_key            varchar(40)  PRIMARY KEY,
    order_id               varchar(64)  NOT NULL,
    order_name             varchar(100) NOT NULL,
    amount                 integer      NOT NULL CHECK (amount > 0),
    status                 varchar(16)  NOT NULL CHECK (status IN
                               ('READY', 'IN_PROGRESS', 'DONE', 'CANCELED', 'ABORTED', 'EXPIRED')),
    -- 구매자 정보 (쇼핑몰이 결제창을 열 때 넘긴 값)
    customer_name          varchar(50),
    customer_email         varchar(255),
    customer_mobile_phone  varchar(20),
    success_url            varchar(500) NOT NULL,
    fail_url               varchar(500) NOT NULL,
    -- 카드 결제 결과
    card_company           varchar(30),
    card_number_masked     varchar(20),
    installment_months     smallint,
    approve_no             varchar(8),
    requested_at           timestamptz  NOT NULL DEFAULT now(),
    authenticated_at       timestamptz,
    approved_at            timestamptz,
    canceled_at            timestamptz,
    cancel_reason          varchar(200),
    cancel_transaction_key varchar(40)
);
CREATE INDEX IF NOT EXISTS ix_payment_order_id ON payment (order_id);
-- 한 주문에 승인된 결제는 하나뿐
CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_order_approved
    ON payment (order_id) WHERE status IN ('DONE', 'CANCELED');
"""


def create_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        # 여러 프로세스가 동시에 떠도 스키마 생성이 겹치지 않게
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('mock-pg-schema'))"))
        conn.execute(text(SCHEMA))
