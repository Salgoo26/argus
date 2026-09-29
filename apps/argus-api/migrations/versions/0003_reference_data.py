"""기준 데이터 — 출처 시스템, 설정 기본값 (db-schema 3-5 setting 시드값)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29

탐지 룰 시드(대량 다운로드)는 탐지 배치와 함께 M3에서 추가한다.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
INSERT INTO source_system (code, name) VALUES
    ('PLATFORM', '커머스 플랫폼'),
    ('ARGUS',    'Argus (자체 접속기록, LOG-17)');

INSERT INTO setting (key, value) VALUES
    ('inspection_cycle',       '"1mo"'),   -- policy 5-2, §8② 기본 월 1회
    ('detection_interval_min', '5'),       -- LOG-03 준실시간 배치
    ('access_log_retention',   '"1y"'),    -- §8① 1년
    ('subject_ids_limit',      '1000');    -- api-spec 2-2
"""

DOWNGRADE_SQL = """
DELETE FROM setting WHERE key IN
    ('inspection_cycle', 'detection_interval_min', 'access_log_retention', 'subject_ids_limit');
DELETE FROM source_system WHERE code IN ('PLATFORM', 'ARGUS');
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
