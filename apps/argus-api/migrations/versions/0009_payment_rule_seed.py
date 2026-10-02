"""탐지 룰 시드 — 결제수단 조회 (기능 레이어 7 ①, db-schema 3-6, policy 1-3)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02

플랫폼 관리자 화면에서 회원의 환불계좌를 **전체 보기**하면 데이터 유형 PAYMENT·수행업무 READ로
기록된다(평소 화면은 끝 4자리만 — 결제수단 조회로 보지 않음). 그 조회를 건마다 탐지한다(상).
결제수단 전체 번호는 업무상 볼 일이 드물어, 볼 때마다 사유(소명)를 받는 통제다.

0008 이후라 룰 변경 이력(CREATE, 변경자 NULL = 시스템)도 함께 남긴다.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
INSERT INTO detection_rule (name, description, rule_type, access_path, severity, condition)
VALUES (
    '결제수단 조회',
    '회원의 결제수단(환불계좌) 전체 번호를 조회한 행위 — 건마다 탐지 (policy 1-3)',
    'EVENT',
    'APP',
    'HIGH',
    '{"all": [{"field": "data_category", "op": "eq", "value": "PAYMENT"},
              {"field": "action", "op": "eq", "value": "READ"}]}'
);

INSERT INTO detection_rule_history (rule_id, version, change_type, snapshot, changed_by, changed_at)
SELECT id, version, 'CREATE',
       jsonb_build_object(
           'id', id, 'name', name, 'description', description, 'rule_type', rule_type,
           'access_path', access_path, 'severity', severity, 'enabled', enabled,
           'auto_request', auto_request, 'condition', condition, 'aggregate', aggregate,
           'group_by', group_by, 'version', version),
       NULL, created_at
  FROM detection_rule
 WHERE name = '결제수단 조회' AND created_by IS NULL;
"""

DOWNGRADE_SQL = """
DELETE FROM detection_rule_history
 WHERE rule_id IN (SELECT id FROM detection_rule
                    WHERE name = '결제수단 조회' AND created_by IS NULL);
DELETE FROM detection_rule WHERE name = '결제수단 조회' AND created_by IS NULL;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
