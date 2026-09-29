"""Walking Skeleton [S] 테이블 11개 (db-schema v0.2 3절 DDL 그대로)

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# 생성 순서 = FK 의존 순서. [S]가 아닌 6개 테이블은 해당 기능 단계에서 추가한다:
# detection_rule_history, explanation_attachment, rule_exception, notification,
# inspection_report, destruction_history
UPGRADE_SQL = """
-- 출처 시스템
CREATE TABLE source_system (
    id          smallserial PRIMARY KEY,
    code        varchar(32)  NOT NULL UNIQUE,      -- 'PLATFORM', 'ARGUS'
    name        varchar(100) NOT NULL,
    created_at  timestamptz  NOT NULL DEFAULT now()
);

-- 취급자 (API ②로 동기화)
CREATE TABLE handler (
    id                 bigserial PRIMARY KEY,
    source_system_id   smallint     NOT NULL REFERENCES source_system(id),
    login_id           varchar(64)  NOT NULL,
    name               varchar(50)  NOT NULL,
    team               varchar(50),
    employment_status  varchar(16)  NOT NULL CHECK (employment_status IN ('ACTIVE','TERMINATED')),
    terminated_at      timestamptz,
    last_event_at      timestamptz  NOT NULL,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (source_system_id, login_id),
    CHECK (employment_status <> 'TERMINATED' OR terminated_at IS NOT NULL)
);

-- Argus 사용자 (A4 OFFICER / A5 HANDLER)
CREATE TABLE argus_user (
    id                  bigserial PRIMARY KEY,
    login_id            varchar(64)  NOT NULL UNIQUE,
    password_hash       varchar(255) NOT NULL,
    role                varchar(16)  NOT NULL CHECK (role IN ('OFFICER','HANDLER')),
    handler_id          bigint       REFERENCES handler(id),
    status              varchar(16)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','LOCKED','DISABLED')),
    failed_login_count  int          NOT NULL DEFAULT 0,
    last_login_at       timestamptz,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (role <> 'HANDLER' OR handler_id IS NOT NULL)
);

-- 접속기록 원장 (§2 3호, §8①③)
CREATE TABLE access_log (
    id                bigserial    PRIMARY KEY,
    event_id          uuid         NOT NULL UNIQUE,
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    access_path       varchar(8)   NOT NULL CHECK (access_path IN ('APP','DB')),
    actor_login_id    varchar(64)  NOT NULL,
    occurred_at       timestamptz  NOT NULL,
    client_ip         inet         NOT NULL,
    subject_type      varchar(16),
    subject_ids       text[],
    subject_count     int          NOT NULL DEFAULT 0,
    subject_truncated boolean      NOT NULL DEFAULT false,
    action            varchar(16)  NOT NULL,
    data_category     varchar(32)  NOT NULL,
    result            varchar(8)   NOT NULL CHECK (result IN ('SUCCESS','FAILURE')),
    request_method    varchar(8),
    request_path      varchar(255),
    request_query_keys text[],
    context           jsonb,
    received_at       timestamptz  NOT NULL DEFAULT now(),
    prev_hash         char(64),
    hash              char(64)     NOT NULL,
    CHECK (action IN ('LOGIN','READ','CREATE','UPDATE','DELETE','DOWNLOAD','EXPORT','UNMASK')),
    CHECK (data_category IN ('MEMBER_BASIC','PAYMENT','ORDER','INQUIRY','ACCESS_LOG','NONE')),
    CHECK (action = 'LOGIN' OR subject_type IS NOT NULL)
);
CREATE INDEX ix_access_log_actor_time ON access_log (source_system_id, actor_login_id, occurred_at);
CREATE INDEX ix_access_log_occurred   ON access_log (occurred_at);
CREATE INDEX ix_access_log_filter     ON access_log (source_system_id, action, occurred_at);
CREATE INDEX ix_access_log_subject    ON access_log USING gin (subject_ids);

-- 탐지 룰
CREATE TABLE detection_rule (
    id           bigserial    PRIMARY KEY,
    name         varchar(100) NOT NULL,
    description  text,
    rule_type    varchar(16)  NOT NULL CHECK (rule_type IN ('EVENT','AGGREGATE')),
    access_path  varchar(8)   NOT NULL DEFAULT 'APP' CHECK (access_path IN ('APP','DB','ALL')),
    severity     varchar(8)   NOT NULL CHECK (severity IN ('HIGH','MEDIUM','LOW')),
    enabled      boolean      NOT NULL DEFAULT true,
    condition    jsonb        NOT NULL,
    aggregate    jsonb,
    group_by     varchar(32)  NOT NULL DEFAULT 'ACTOR_RULE_DATE',
    version      int          NOT NULL DEFAULT 1,
    created_by   bigint       REFERENCES argus_user(id),
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    CHECK ((rule_type = 'AGGREGATE') = (aggregate IS NOT NULL))
);

-- 탐지건
CREATE TABLE detection (
    id                 bigserial    PRIMARY KEY,
    rule_id            bigint       NOT NULL REFERENCES detection_rule(id),
    rule_version       int          NOT NULL,
    rule_snapshot      jsonb        NOT NULL,
    source_system_id   smallint     NOT NULL REFERENCES source_system(id),
    actor_login_id     varchar(64)  NOT NULL,
    group_bucket       varchar(64)  NOT NULL,
    severity           varchar(8)   NOT NULL,
    status             varchar(16)  NOT NULL DEFAULT 'DETECTED'
                       CHECK (status IN ('DETECTED','REQUESTED','SUBMITTED','APPROVED',
                                         'REJECTED','DISMISSED','ESCALATED')),
    round              int          NOT NULL DEFAULT 0,
    log_count          int          NOT NULL DEFAULT 0,
    aggregate_value    numeric,
    log_summary        jsonb,
    first_occurred_at  timestamptz  NOT NULL,
    last_occurred_at   timestamptz  NOT NULL,
    detected_at        timestamptz  NOT NULL DEFAULT now(),
    closed_at          timestamptz,
    close_reason       text
);
-- 같은 그룹의 "진행 중" 탐지건은 1개만 (policy 2-3)
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, actor_login_id, group_bucket)
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');
CREATE INDEX ix_detection_status ON detection (status, detected_at);
CREATE INDEX ix_detection_actor  ON detection (source_system_id, actor_login_id);

-- 탐지건 하위 로그
CREATE TABLE detection_log (
    detection_id   bigint NOT NULL REFERENCES detection(id)  ON DELETE CASCADE,
    access_log_id  bigint NOT NULL REFERENCES access_log(id) ON DELETE CASCADE,
    PRIMARY KEY (detection_id, access_log_id)
);
CREATE INDEX ix_detection_log_log ON detection_log (access_log_id);

-- 상태 전이 이력 (감사 추적)
CREATE TABLE detection_status_history (
    id             bigserial   PRIMARY KEY,
    detection_id   bigint      NOT NULL REFERENCES detection(id),
    from_status    varchar(16),
    to_status      varchar(16) NOT NULL,
    round          int         NOT NULL,
    actor_user_id  bigint      REFERENCES argus_user(id),
    comment        text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_detection_status_hist ON detection_status_history (detection_id, created_at);

-- 소명 (차수별 1행)
CREATE TABLE explanation (
    id               bigserial   PRIMARY KEY,
    detection_id     bigint      NOT NULL REFERENCES detection(id),
    round            int         NOT NULL,
    requested_by     bigint      NOT NULL REFERENCES argus_user(id),
    requested_at     timestamptz NOT NULL DEFAULT now(),
    request_message  text,
    submitted_by     bigint      REFERENCES argus_user(id),
    submitted_at     timestamptz,
    content          text,
    reviewed_by      bigint      REFERENCES argus_user(id),
    reviewed_at      timestamptz,
    review_result    varchar(16) CHECK (review_result IN ('APPROVED','REJECTED')),
    review_comment   text,
    UNIQUE (detection_id, round)
);

-- 탐지 배치 실행 이력 + 커서
CREATE TABLE detection_batch_run (
    id                  bigserial   PRIMARY KEY,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    from_access_log_id  bigint      NOT NULL,
    to_access_log_id    bigint      NOT NULL,
    processed_count     int         NOT NULL DEFAULT 0,
    detected_count      int         NOT NULL DEFAULT 0,
    status              varchar(16) NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    error               text
);

-- 설정
CREATE TABLE setting (
    key         varchar(64) PRIMARY KEY,
    value       jsonb       NOT NULL,
    updated_by  bigint      REFERENCES argus_user(id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
"""

DOWNGRADE_SQL = """
DROP TABLE setting;
DROP TABLE detection_batch_run;
DROP TABLE explanation;
DROP TABLE detection_status_history;
DROP TABLE detection_log;
DROP TABLE detection;
DROP TABLE detection_rule;
DROP TABLE access_log;
DROP TABLE argus_user;
DROP TABLE handler;
DROP TABLE source_system;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
