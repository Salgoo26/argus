"""회원가입 동의 구조를 법적 근거별로 — 계약 이행 항목은 동의 대신 안내 (PIPA §15①4, §22③)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10

- 2023.3 개정으로 계약 이행에 필요한 개인정보는 동의 없이 수집할 수 있고(§15①4), 동의 없이
  처리하는 항목은 법적 근거와 함께 동의 항목과 구분해 알린다(§22③ — 처리방침·가입 화면 안내).
  그래서 "개인정보 수집·이용 (필수) 동의"(PRIVACY_REQUIRED)를 더는 받지 않는다
- 항목 행과 지난 동의 이력(member_consent)은 지우지 않는다 — 그때 받은 동의의 증적이고,
  member_consent.item_code가 이 행을 참조한다. 대신 consent_item.active로 사용을 끈다
  (가입 화면·가입 검증·마이페이지 동의 내역은 active 항목만)
- 이용약관(TOS) v2: 0006처럼 문안을 고쳐 쓰지 않고 버전·시행일을 올린다
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
-- 받지 않는 동의 항목 — 행은 지난 동의 이력의 증적이라 남긴다
ALTER TABLE consent_item ADD COLUMN active boolean NOT NULL DEFAULT true;

UPDATE consent_item SET active = false WHERE code = 'PRIVACY_REQUIRED';

UPDATE consent_item
SET version = 'v2',
    purpose = '서비스 이용 계약의 체결',
    items = '— (이용약관 전문은 이용약관 페이지)',
    retention = '회원 탈퇴 시까지',
    effective_from = '2026-10-11T00:00:00+09:00'
WHERE code = 'TOS';
"""

DOWNGRADE_SQL = """
UPDATE consent_item
SET version = 'v1',
    effective_from = '2026-10-01T00:00:00+09:00'
WHERE code = 'TOS';

ALTER TABLE consent_item DROP COLUMN active;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
