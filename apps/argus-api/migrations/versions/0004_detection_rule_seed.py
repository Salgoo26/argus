"""탐지 룰 시드 — 대량 다운로드 (db-schema 3-6 기본 룰 시드, policy 1-3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01

Walking Skeleton은 [S] 표시된 "대량 다운로드" 하나만 넣는다.
나머지 기본 룰(야간·주말·퇴직자 계정 접속 등)은 Skeleton 이후 단계에서
같은 방식(마이그레이션 한 줄)으로 추가한다 — v0.1은 룰 빌더 UI가 없다.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
INSERT INTO detection_rule (name, description, rule_type, access_path, severity, condition)
VALUES (
    '대량 다운로드',
    '한 번의 다운로드로 정보주체 50건 이상을 내려받은 행위.'
    || ' 다운로드 기록에는 처리 건수가 함께 남으므로 (§2 3호 처리한 정보주체 정보)'
    || ' 기록 1건만으로 판정한다 (policy 1-3)',
    'EVENT',
    'APP',
    'HIGH',
    '{"all": [{"field": "action", "op": "eq", "value": "DOWNLOAD"},
              {"field": "subject_count", "op": "gte", "value": 50}]}'
);
"""

DOWNGRADE_SQL = """
DELETE FROM detection_rule WHERE name = '대량 다운로드' AND created_by IS NULL;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
