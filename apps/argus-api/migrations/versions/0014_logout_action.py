"""수행업무 코드 LOGOUT 추가 (v0.1 보강 A — 갭 A6, 안내서 FAQ 147 "로그인·로그아웃·로그인 실패")

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10

access_log의 CHECK 두 개만 바꾼다 — 행 자체는 고치지 않는다(append-only 트리거는 UPDATE만 막고,
DDL은 소유자가 마이그레이션으로 한다). LOGOUT은 LOGIN처럼 데이터 유형 NONE·정보주체 없음.
해시체인 정규화 규칙은 컬럼 값만 보므로 영향 없음.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE access_log DROP CONSTRAINT access_log_action_check;
ALTER TABLE access_log ADD CONSTRAINT access_log_action_check CHECK (action IN
    ('LOGIN','LOGOUT','READ','CREATE','UPDATE','DELETE','DOWNLOAD','EXPORT','UNMASK'));
ALTER TABLE access_log DROP CONSTRAINT access_log_check;
ALTER TABLE access_log ADD CONSTRAINT access_log_check
    CHECK (action IN ('LOGIN','LOGOUT') OR subject_type IS NOT NULL);
"""

# LOGOUT 기록이 이미 있으면 되돌릴 수 없다(원장은 지우지 않는다) — 그때는 실패하는 게 맞다
DOWNGRADE_SQL = """
ALTER TABLE access_log DROP CONSTRAINT access_log_check;
ALTER TABLE access_log ADD CONSTRAINT access_log_check
    CHECK (action = 'LOGIN' OR subject_type IS NOT NULL);
ALTER TABLE access_log DROP CONSTRAINT access_log_action_check;
ALTER TABLE access_log ADD CONSTRAINT access_log_action_check CHECK (action IN
    ('LOGIN','READ','CREATE','UPDATE','DELETE','DOWNLOAD','EXPORT','UNMASK'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
