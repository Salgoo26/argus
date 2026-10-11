"""동의 문안 — 개인정보 수집·이용(필수) v3, 이용약관 v2, 동의 항목 사용 여부 (2026-10-11)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10

- PRIVACY_REQUIRED v3: 동의받을 때 알리는 수집 항목(§15②2호)을 처리방침 v3 2절과 맞춘다 —
  주문 정보·문의 내용을 항목에 넣는다. 목적·보유 기간은 v2 그대로
- TOS v2: 이용약관 정식 문안. 목적·항목·보유 기간 칸은 v1 그대로(약관은 개인정보 수집 동의가 아님)
- 둘 다 0006처럼 문안을 고쳐 쓰지 않고 **버전·시행일을 올린다** — member_consent.item_version에
  남은 지난 동의는 "그때 알린 항목"의 증적이다
- consent_item.active: 나중에 항목을 더는 받지 않게 될 때 행을 지우지 않고 끄는 장치(기본 true).
  지난 동의 이력이 이 행을 참조하고 증적이므로 삭제 대신 사용 여부로 다룬다. 지금 끈 항목은 없다
- 기존 회원의 새 버전 재동의 절차는 만들지 않는다
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE consent_item ADD COLUMN active boolean NOT NULL DEFAULT true;

UPDATE consent_item
SET version = 'v3',
    items = '이메일, 비밀번호, 이름, 휴대전화번호 / (마이페이지에서 등록 시) '
            '배송지(받는 사람·연락처·주소), 환불계좌 / (주문·문의 시) '
            '주문 정보(상품·금액·배송 정보·결제 결과), 문의 내용',
    effective_from = '2026-10-11T00:00:00+09:00'
WHERE code = 'PRIVACY_REQUIRED';

UPDATE consent_item
SET version = 'v2',
    effective_from = '2026-10-11T00:00:00+09:00'
WHERE code = 'TOS';
"""

DOWNGRADE_SQL = """
UPDATE consent_item
SET version = 'v1',
    effective_from = '2026-10-01T00:00:00+09:00'
WHERE code = 'TOS';

UPDATE consent_item
SET version = 'v2',
    items = '이메일, 비밀번호, 이름, 휴대전화번호 / (마이페이지에서 등록 시) '
            '배송지(받는 사람·연락처·주소), 환불계좌',
    effective_from = '2026-10-07T00:00:00+09:00'
WHERE code = 'PRIVACY_REQUIRED';

ALTER TABLE consent_item DROP COLUMN active;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
