"""점검 보고서 이력 (기능 레이어 9 — LOG-09, §8② 점검 증적)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07

db-schema 3절 DDL 그대로. v0.1은 **마스킹 보고서만** 만든다(policy 6-1 — 언마스킹 보고서는
언마스킹 기능과 함께 v0.2) → `unmasked`는 늘 false다.
보고서 파일은 서버에 두지 않고(`file_path` 비움) 생성 시점의 집계
스냅샷(`summary`)을 남겨, 나중에 데이터가 바뀌어도 "그때 보고한 내용"을 다시 그릴 수 있게 한다.

앱 계정은 조회·추가만 — 보고서 이력은 고치거나 지우지 않는다(점검 증적).
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE inspection_report (
    id              bigserial   PRIMARY KEY,
    period_from     timestamptz NOT NULL,
    period_to       timestamptz NOT NULL,
    scope           jsonb,
    summary         jsonb       NOT NULL,
    escalated_count int         NOT NULL DEFAULT 0,
    unmasked        boolean     NOT NULL DEFAULT false,
    unmask_reason   text,
    file_path       varchar(500),
    generated_by    bigint      NOT NULL REFERENCES argus_user(id),
    generated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (period_from < period_to),
    CHECK (NOT unmasked OR unmask_reason IS NOT NULL)
);
CREATE INDEX ix_inspection_report_generated ON inspection_report (generated_at DESC);

GRANT SELECT, INSERT ON inspection_report TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE inspection_report_id_seq TO argus_app;
"""

DOWNGRADE_SQL = """
DROP TABLE inspection_report;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
