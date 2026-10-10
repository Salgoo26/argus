"""소명 단위 보완 (v0.1 보강 J — 개선점 통합검토 4-5)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-10

J-1 제출 뒤에 붙은 기록
- `detection_log.attached_at` — 하위 기록이 탐지건에 붙은 시각. 소명 제출 시각과 비교해 "이 소명이
  다루지 않은 기록"을 가린다. 기본값은 `clock_timestamp()`(트랜잭션 시작 시각이 아니라 실제로 붙인
  순간 — 순찰 트랜잭션이 탐지건 잠금을 기다리는 사이 제출이 끼어든 경우도 바르게 비교)
- 이미 있는 행은 NULL(붙은 시각 모름) — 제출 뒤 기록으로 세지 않는다

J-2 다른 성격의 처리는 다른 탐지건 (EVENT 룰만)
- `detection.data_category`·`detection.action_group` — EVENT 탐지건의 묶음 기준에 데이터 유형과
  행위 구분(조회 READ / 내려받기 DOWNLOAD / 변경·삭제 CHANGE / 로그인·로그아웃 SESSION)을 더한다
- AGGREGATE 탐지건·기존 탐지건은 NULL(그대로 둔다). 진행 중 건 유일성 인덱스도 새 키로 —
  NULL끼리 같은 값으로 보도록 NULLS NOT DISTINCT(PostgreSQL 15+)
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE detection_log ADD COLUMN attached_at timestamptz;
ALTER TABLE detection_log ALTER COLUMN attached_at SET DEFAULT clock_timestamp();

ALTER TABLE detection
    ADD COLUMN data_category varchar(16),
    ADD COLUMN action_group  varchar(16)
        CHECK (action_group IN ('READ','DOWNLOAD','CHANGE','SESSION'));

DROP INDEX ux_detection_open_group;
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, access_path, actor_login_id, group_bucket,
                  data_category, action_group)
    NULLS NOT DISTINCT
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');
"""

DOWNGRADE_SQL = """
DROP INDEX ux_detection_open_group;
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, access_path, actor_login_id, group_bucket)
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');

ALTER TABLE detection DROP COLUMN action_group, DROP COLUMN data_category;
ALTER TABLE detection_log DROP COLUMN attached_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
