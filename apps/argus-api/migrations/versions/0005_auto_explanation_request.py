"""자동 소명 요청 (2026-10-01 사용자 확정 — implementation-log M4 착수)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01

- detection_rule.auto_request: 탐지 시 소명 요청을 자동으로 보낼지. 기본 켜짐(룰 빌더에서 룰별 조정)
- explanation.requested_by NULL 허용: NULL = 시스템(탐지 배치)이 보낸 자동 요청
  (detection_status_history.actor_user_id의 "NULL = 시스템"과 같은 규칙)

새 테이블이 없어 권한 변경은 없다 (argus_app은 두 테이블에 SELECT·INSERT·UPDATE 보유).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE detection_rule ADD COLUMN auto_request boolean NOT NULL DEFAULT true;
ALTER TABLE explanation ALTER COLUMN requested_by DROP NOT NULL;
"""

# 시스템 요청(NULL)이 남아 있으면 NOT NULL로 되돌릴 수 없다
# — 자동 요청 기록을 지우지 않고 실패시킨다
DOWNGRADE_SQL = """
ALTER TABLE explanation ALTER COLUMN requested_by SET NOT NULL;
ALTER TABLE detection_rule DROP COLUMN auto_request;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
