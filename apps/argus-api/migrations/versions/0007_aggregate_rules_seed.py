"""탐지 룰 시드 — 대량 조회·전월 대비 급증 (기능 레이어 4, db-schema 3-6, policy 1-3)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01

AGGREGATE 룰 — 조건식으로 대상(조회)을 좁힌 뒤 윈도우의 집계값을 기준과 비교한다 (policy 1-1).
- 대량 조회: 한 시간(매시 정각부터, KST) 동안 조회 기록 100건 이상
- 전월 대비 급증: 당월 누적 조회 기록 ≥ 전월 같은 기간 × 2. 전월 같은 기간 기록이 20건 미만이면
  판정하지 않는다(min_baseline — 기준선이 몇 건뿐이면 비율이 쉽게 튐)
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
INSERT INTO detection_rule
    (name, description, rule_type, access_path, severity, condition, aggregate)
VALUES
(
    '대량 조회',
    '한 시간(매시 정각부터, 한국 시각) 동안 개인정보 조회가 100건 이상인 행위.'
    || ' 조회 한 번은 적어도 기록 수로 세므로 짧은 시간의 반복 조회를 잡는다 (policy 1-3)',
    'AGGREGATE',
    'APP',
    'HIGH',
    '{"all": [{"field": "action", "op": "eq", "value": "READ"}]}',
    '{"window": "1h", "measure": "LOG_COUNT", "compare": "ABSOLUTE", "threshold": 100}'
),
(
    '전월 대비 급증',
    '이달 1일부터 지금까지의 조회 기록이 지난달 같은 기간의 2배 이상. 평소보다 갑자기 늘어난'
    || ' 조회를 잡는다. 지난달 같은 기간 기록이 20건 미만이면(신규 취급자·월초 등) 비율을 믿을 수'
    || ' 없어 판정하지 않는다 (policy 1-3)',
    'AGGREGATE',
    'APP',
    'MEDIUM',
    '{"all": [{"field": "action", "op": "eq", "value": "READ"}]}',
    '{"window": "1mo", "measure": "LOG_COUNT", "compare": "RATIO_TO_BASELINE",
      "baseline": "PREV_MONTH_SAME_PERIOD", "threshold": 2.0, "min_baseline": 20}'
);
"""

DOWNGRADE_SQL = """
DELETE FROM detection_rule
 WHERE name IN ('대량 조회', '전월 대비 급증') AND created_by IS NULL;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
