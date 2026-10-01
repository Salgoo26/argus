"""탐지 룰 시드 — 야간·주말·퇴직자 계정 접속 (기능 레이어 1, db-schema 3-6, policy 1-3)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01

0004(대량 다운로드)와 같은 방식 — v0.1은 룰 빌더 UI가 없어 기본 룰을 마이그레이션으로 넣는다.
시각·요일은 한국 시각(KST) 기준, 야간 구간은 22:00 포함 ~ 06:00 미포함.
퇴직자 계정 접속은 auto_request가 켜져 있어도 소명 요청이 가지 않는다 — 퇴직한 취급자는
A5 계정이 비활성이라 받을 사람이 없고, 퇴직자 접속은 원래 담당자 사안이다 (policy 3-2).
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
INSERT INTO detection_rule (name, description, rule_type, access_path, severity, condition)
VALUES
(
    '야간 접속',
    '업무 시간 밖(22:00~06:00, 한국 시각)에 개인정보를 조회·다운로드한 행위 (policy 1-3)',
    'EVENT',
    'APP',
    'MEDIUM',
    '{"all": [{"field": "action", "op": "in", "value": ["READ", "DOWNLOAD"]},
              {"field": "occurred_time", "op": "between", "value": ["22:00", "06:00"]}]}'
),
(
    '주말 접속',
    '토·일요일(한국 시각)에 개인정보처리시스템에 접속한 행위 (policy 1-3)',
    'EVENT',
    'APP',
    'LOW',
    '{"all": [{"field": "occurred_weekday", "op": "in", "value": ["SAT", "SUN"]}]}'
),
(
    '퇴직자 계정 접속',
    '행위 시점에 이미 퇴직한 취급자 계정의 모든 접속. 현재 재직상태가 아니라 퇴직 시각과'
    || ' 접속 시각을 비교한다. 취급자 명부에 없는 계정도 판정할 수 없어 탐지한다 (policy 1-3)',
    'EVENT',
    'APP',
    'HIGH',
    '{"all": [{"field": "actor_terminated_at_or_before", "op": "eq", "value": true}]}'
);
"""

DOWNGRADE_SQL = """
DELETE FROM detection_rule
 WHERE name IN ('야간 접속', '주말 접속', '퇴직자 계정 접속') AND created_by IS NULL;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
