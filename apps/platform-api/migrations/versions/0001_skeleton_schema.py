"""Walking Skeleton [S] 테이블 3개 (db-schema 4절 DDL 그대로)

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# [S]가 아닌 테이블(consent_item, member_consent, payment_method, product, orders, inquiry,
# operator_permission_history, retained_member_record, destruction_history)은
# 해당 기능 단계에서 추가한다
UPGRADE_SQL = """
-- 취급자 계정 (관리자 UI 사용자, API ② 동기화 원천)
CREATE TABLE operator (
    id                 bigserial    PRIMARY KEY,
    login_id           varchar(64)  NOT NULL UNIQUE,
    password_hash      varchar(255) NOT NULL,
    name               varchar(50)  NOT NULL,
    team               varchar(50)  NOT NULL CHECK (team IN ('CS','MARKETING','OPS')),
    role               varchar(16)  NOT NULL CHECK (role IN ('ADMIN','CS','MARKETING','OPS')),
    employment_status  varchar(16)  NOT NULL DEFAULT 'ACTIVE'
                       CHECK (employment_status IN ('ACTIVE','TERMINATED')),
    terminated_at      timestamptz,
    failed_login_count int          NOT NULL DEFAULT 0,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now()
);

-- 회원 (정보주체)
CREATE TABLE member (
    id             bigserial    PRIMARY KEY,           -- 접속기록에는 이 값만 전송
    email          varchar(255) NOT NULL UNIQUE,
    password_hash  varchar(255) NOT NULL,
    name           varchar(50)  NOT NULL,
    phone          varchar(20),
    address        varchar(255),
    status         varchar(16)  NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','WITHDRAWN')),
    created_at     timestamptz  NOT NULL DEFAULT now(),
    withdrawn_at   timestamptz,
    CHECK (status <> 'WITHDRAWN' OR withdrawn_at IS NOT NULL)
);

-- outbox (Argus 전송 대기) — 전송 확인 후 삭제되는 임시 버퍼, 원장은 Argus access_log
CREATE TABLE outbox (
    id              bigserial    PRIMARY KEY,
    event_id        uuid         NOT NULL UNIQUE,
    topic           varchar(16)  NOT NULL CHECK (topic IN ('ACCESS_LOG','HANDLER')),
    payload         jsonb        NOT NULL,             -- API 명세 2-1 / 3-1의 이벤트 1건
    status          varchar(16)  NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','DEAD')),
    attempts        int          NOT NULL DEFAULT 0,
    next_retry_at   timestamptz  NOT NULL DEFAULT now(),
    last_error      text,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_outbox_pending ON outbox (status, next_retry_at) WHERE status = 'PENDING';
"""

DOWNGRADE_SQL = """
DROP TABLE outbox;
DROP TABLE member;
DROP TABLE operator;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
