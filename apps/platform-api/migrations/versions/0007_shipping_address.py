"""배송지 (기능 레이어 7-4 ②, db-schema 4절 "플랫폼 보강", PLT-03)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07

- shipping_address 신규: 회원별 배송지 여러 개, 회원당 기본 배송지 1개(부분 유니크 인덱스)
- 기존 member.address는 그 회원의 **기본 배송지로 옮긴 뒤** 컬럼을 지운다 — 주소는 회원 정보가
  아니라 배송지(policy 4-3). 받는 사람·연락처는 회원 이름·휴대폰으로 채운다
- 우편번호·받는 사람 연락처는 API에서 필수지만 DB는 NULL 허용: 옮겨 온 주소에는 우편번호가 없고,
  휴대폰 없이 가입한 기존 고객도 있을 수 있다(가짜 값을 채우지 않는다)
- member_id는 FK만(ON DELETE 없음) — 탈퇴 처리(shop/withdrawal.py)가 명시적으로 지운다
  (환불계좌와 같음)
- 게이트웨이 데이터 유형은 MEMBER_BASIC (apps/db-gateway/app/sql.py TABLE_CATEGORY)
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE shipping_address (
    id              bigserial    PRIMARY KEY,
    member_id       bigint       NOT NULL REFERENCES member(id),
    label           varchar(30)  NOT NULL,          -- 배송지 이름 ('집', '회사')
    recipient       varchar(50)  NOT NULL,          -- 받는 사람
    phone           varchar(20),                    -- 받는 사람 연락처 (API 필수)
    zip_code        varchar(5),                     -- 우편번호 5자리 (API 필수)
    address         varchar(255) NOT NULL,          -- 주소
    address_detail  varchar(100),                   -- 상세주소
    is_default      boolean      NOT NULL DEFAULT false,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_shipping_address_member ON shipping_address (member_id, id);
-- 회원당 기본 배송지 1개
CREATE UNIQUE INDEX ux_shipping_address_default ON shipping_address (member_id) WHERE is_default;

INSERT INTO shipping_address (member_id, label, recipient, phone, address, is_default)
SELECT id, '기본 배송지', name, phone, btrim(address), true
FROM member
WHERE address IS NOT NULL AND btrim(address) <> '';

ALTER TABLE member DROP COLUMN address;
"""

DOWNGRADE_SQL = """
ALTER TABLE member ADD COLUMN address varchar(255);
UPDATE member m
SET address = left(concat_ws(' ', s.address, s.address_detail), 255)
FROM shipping_address s
WHERE s.member_id = m.id AND s.is_default;
DROP TABLE shipping_address;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
