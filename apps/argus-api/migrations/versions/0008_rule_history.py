"""룰 변경 이력 (기능 레이어 6 — 룰 빌더, LOG-13, db-schema 3-3)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01

- detection_rule_history: 룰을 만들고·고치고·켜고·끌 때마다 **변경 후 룰 전체**(snapshot)와
  누가(changed_by)·언제를 남긴다. 사유는 받지 않는다(2026-10-01 사용자 결정 — 무엇이 바뀌었는지는
  스냅숏으로 남는다)
- 앱 계정은 **추가·조회만** — 상태 이력(detection_status_history)과 같은 감사 추적 테이블이다
- (rule_id, version) 유일 — 같은 버전이 두 번 기록되지 않게(동시 수정 방지와 같은 축)
- 기존 룰(마이그레이션 시드)은 CREATE 이력을 소급해 남긴다 — changed_by NULL = 시스템(마이그레이션)
- 룰 삭제는 없다(2026-10-01 사용자 결정): 탐지건이 룰을 참조하고, "그 시점에 어떤 룰을 운영했나"가
  점검 근거다. 앱 계정에는 detection_rule DELETE 권한이 원래 없다(0002)
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE detection_rule_history (
    id           bigserial   PRIMARY KEY,
    rule_id      bigint      NOT NULL REFERENCES detection_rule(id),
    version      int         NOT NULL,
    change_type  varchar(16) NOT NULL CHECK (change_type IN ('CREATE','UPDATE','ENABLE','DISABLE')),
    snapshot     jsonb       NOT NULL,             -- 변경 후 룰 전체
    changed_by   bigint      REFERENCES argus_user(id),   -- NULL = 시스템(마이그레이션)
    changed_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rule_id, version)
);
CREATE INDEX ix_rule_history ON detection_rule_history (rule_id, changed_at);

GRANT SELECT, INSERT ON detection_rule_history TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE detection_rule_history_id_seq TO argus_app;

INSERT INTO detection_rule_history (rule_id, version, change_type, snapshot, changed_by, changed_at)
SELECT id, version, 'CREATE',
       jsonb_build_object(
           'id', id, 'name', name, 'description', description, 'rule_type', rule_type,
           'access_path', access_path, 'severity', severity, 'enabled', enabled,
           'auto_request', auto_request, 'condition', condition, 'aggregate', aggregate,
           'group_by', group_by, 'version', version),
       NULL, created_at
  FROM detection_rule
 ORDER BY id;
"""

DOWNGRADE_SQL = """
DROP TABLE detection_rule_history;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
