"""Argus 계정 이력 (v0.1 보강 L-4 — 고시 §5①③, 안내서 61~62)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-10

담당자(OFFICER)·취급자(HANDLER) 계정의 권한 부여·변경·말소를 남긴다 — 플랫폼
operator_permission_history와 같은 구조.

| 구분 | 뜻 |
| GRANT   | 담당자 권한 부여(취급자 → 담당자 전환, 담당자 계정 생성) · 동기화로 취급자 계정 생성 |
| CHANGE  | 담당자 → 취급자 |
| REVOKE  | 비활성화(말소) — 화면 또는 플랫폼 퇴직 동기화 |
| RESTORE | 재활성화 |
| UNLOCK  | 로그인 5회 실패 잠금 해제 |

- **사유 필수**, append-only(UPDATE·DELETE 트리거로 거부). 앱 계정은 SELECT·INSERT만
- 계정·처리자는 id와 **아이디를 함께** 남기고 FK를 두지 않는다
  — 계정 행이 정리돼도 이력은 남아야 한다
- 처리자 NULL = 운영 스크립트(app.scripts.users)·명부 동기화(시스템)
- 이미 있는 계정은 GRANT 1건씩(사유 "초기 계정")으로 채운다
- 이 화면·API의 행위는 Argus 자체 접속기록에서 제외(정보주체 처리 아님) — 이 이력이 증적
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE argus_user_history (
    id              bigserial    PRIMARY KEY,
    user_id         bigint       NOT NULL,
    login_id        varchar(64)  NOT NULL,
    change_type     varchar(8)   NOT NULL
                    CHECK (change_type IN ('GRANT','CHANGE','REVOKE','RESTORE','UNLOCK')),
    before_role     varchar(16),
    after_role      varchar(16)  NOT NULL,
    before_status   varchar(16),
    after_status    varchar(16)  NOT NULL,
    reason          varchar(500) NOT NULL CHECK (length(btrim(reason)) > 0),
    actor_user_id   bigint,
    actor_login_id  varchar(64),
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_argus_user_history_created ON argus_user_history (created_at DESC);
CREATE INDEX ix_argus_user_history_user ON argus_user_history (user_id, created_at DESC);

CREATE FUNCTION forbid_argus_user_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'argus_user_history is append-only (% blocked)', TG_OP; END $$;
CREATE TRIGGER trg_argus_user_history_append_only
    BEFORE UPDATE OR DELETE ON argus_user_history
    FOR EACH ROW EXECUTE FUNCTION forbid_argus_user_history_change();

GRANT SELECT, INSERT ON argus_user_history TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE argus_user_history_id_seq TO argus_app;

INSERT INTO argus_user_history
    (user_id, login_id, change_type, after_role, after_status, reason, created_at)
SELECT id, login_id, 'GRANT', role, status, '초기 계정', created_at
  FROM argus_user;
"""

DOWNGRADE_SQL = """
DROP TABLE argus_user_history;
DROP FUNCTION forbid_argus_user_history_change();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
