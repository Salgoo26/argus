"""DB 접속 토큰 발급 기록 (기능 레이어 8 — 2티어, architecture 3-4)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06

db-schema 4절 DDL 그대로. **감사용 기록일 뿐 검증에 쓰지 않는다** — 게이트웨이는 서명 토큰(JWT)의
서명·만료만 검증하고 이 테이블을 조회하지 않는다. 공용 계정이 DB 소유자라 게이트웨이로 접속한 사람이
이 테이블을 고칠 수 있으므로, 검증 근거로 쓰면 남의 토큰 행을 넣어 위장할 수 있기 때문.
토큰 값·해시는 저장하지 않는다(발급 화면에 한 번만 표시).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE db_access_token (
    token_id     uuid         PRIMARY KEY,
    operator_id  bigint       NOT NULL REFERENCES operator(id),
    issued_at    timestamptz  NOT NULL DEFAULT now(),
    expires_at   timestamptz  NOT NULL,
    issued_ip    inet         NOT NULL,
    CHECK (expires_at = issued_at + interval '1 hour')
);
CREATE INDEX ix_db_access_token_operator ON db_access_token (operator_id, issued_at);
"""

DOWNGRADE_SQL = """
DROP TABLE db_access_token;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
