"""1:1 문의 (기능 레이어 7 ②, PLT-06·PLT-17, 결정 8 — 고객 → CS)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02

db-schema 4절 DDL 그대로. 문의는 CS가 고객 정보를 조회하는 **업무 근거**다 — 관리자 문의 화면의
접속기록에는 context.ticket_id(INQ-문의번호)가 실린다(api-spec 2-4 "문의 상세 확인").
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE inquiry (
    id           bigserial    PRIMARY KEY,
    member_id    bigint       REFERENCES member(id) ON DELETE SET NULL,
    title        varchar(200) NOT NULL,
    body         text         NOT NULL,
    status       varchar(16)  NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ANSWERED')),
    answer       text,
    answered_by  bigint       REFERENCES operator(id),
    created_at   timestamptz  NOT NULL DEFAULT now(),
    answered_at  timestamptz
);
CREATE INDEX ix_inquiry_status ON inquiry (status, created_at DESC);
CREATE INDEX ix_inquiry_member ON inquiry (member_id, created_at DESC);
"""

DOWNGRADE_SQL = """
DROP TABLE inquiry;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
