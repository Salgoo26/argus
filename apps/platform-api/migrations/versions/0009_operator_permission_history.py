"""관리자 계정·권한 이력 (v0.1 보강 L-2·L-3 — 고시 §5①③, 안내서 61~62)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10

- operator_permission_history: 개인정보취급자 권한의 부여(GRANT)·변경(CHANGE)·말소(REVOKE) 기록.
  변경 전·후 역할/팀/재직 상태, **사유(필수)**, 처리자, 일시. §5③ 최소 3년 보관 — 파기 배치(v0.2)와
  함께 다루고 지금은 삭제 경로가 없다
- **append-only**: UPDATE·DELETE는 트리거로 거부(소유자 포함 — 플랫폼은 DB 계정이 하나라 권한으로
  막을 수 없다). 처리자 FK는 시스템(시드·마이그레이션)이면 NULL
- operator.must_change_password: 계정 관리 화면에서 만든 계정은 임시 비밀번호 →
  첫 로그인 때 변경 강제
- 이미 있는 계정(개발 스택)은 GRANT 1건씩(사유 "초기 계정")으로 이력을 채운다. 새로 시드하는 계정은
  시드 스크립트가 같은 방식으로 남긴다

게이트웨이 분류: 취급자(직원) 정보라 `operator`와 같이 회원 개인정보 유형으로 분류하지 않는다
(TABLE_CATEGORY 미등록 = NONE — architecture 3-4 분류는 정보주체(회원) 기준)
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE operator ADD COLUMN must_change_password boolean NOT NULL DEFAULT false;

CREATE TABLE operator_permission_history (
    id              bigserial    PRIMARY KEY,
    operator_id     bigint       NOT NULL REFERENCES operator(id),
    change_type     varchar(8)   NOT NULL CHECK (change_type IN ('GRANT','CHANGE','REVOKE')),
    before_role     varchar(16),
    after_role      varchar(16)  NOT NULL,
    before_team     varchar(50),
    after_team      varchar(50)  NOT NULL,
    before_status   varchar(16),
    after_status    varchar(16)  NOT NULL,
    reason          varchar(500) NOT NULL CHECK (length(btrim(reason)) > 0),
    actor_id        bigint       REFERENCES operator(id),
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_operator_permission_history_target
    ON operator_permission_history (operator_id, created_at DESC);
CREATE INDEX ix_operator_permission_history_created
    ON operator_permission_history (created_at DESC);

CREATE FUNCTION forbid_permission_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'operator_permission_history is append-only (% blocked)', TG_OP; END $$;
CREATE TRIGGER trg_permission_history_append_only
    BEFORE UPDATE OR DELETE ON operator_permission_history
    FOR EACH ROW EXECUTE FUNCTION forbid_permission_history_change();

INSERT INTO operator_permission_history
    (operator_id, change_type, after_role, after_team, after_status, reason, created_at)
SELECT id, 'GRANT', role, team, 'ACTIVE', '초기 계정', created_at
  FROM operator;
"""

DOWNGRADE_SQL = """
DROP TABLE operator_permission_history;
DROP FUNCTION forbid_permission_history_change();
ALTER TABLE operator DROP COLUMN must_change_password;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
