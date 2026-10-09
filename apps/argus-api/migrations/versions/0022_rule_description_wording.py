"""기본 룰 설명에서 정책·조항 번호를 뺀다 (v0.1 보강 M — 화면 문구 정리)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-10

룰 설명은 룰 목록·탐지건 상세에 그대로 보인다. 시드 룰 설명 끝의 "(policy 1-3)"·"(§2 3호 …)"
같은 설계 문서 번호는 담당자·취급자에게 뜻이 없어 지운다.

- 시스템이 만든 룰(created_by NULL) 중 **담당자가 한 번도 고치지 않은 룰만**
  — 사람이 쓴 문장은 건드리지 않는다
- 룰 변경은 언제나 이력으로 남는다(기능 레이어 6)
  — 버전 +1, 변경 이력 UPDATE(변경자 NULL = 시스템)
- 되돌리기(downgrade)는 하지 않는다 — 문구 정리이고, 이력에 이전 문장이 남아 있다
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TEMP TABLE reworded ON COMMIT DROP AS
SELECT r.id
  FROM detection_rule r
 WHERE r.created_by IS NULL
   AND r.description ~ '\s*\((policy [0-9-]+|§[^)]*)\)'
   AND NOT EXISTS (SELECT 1 FROM detection_rule_history h
                    WHERE h.rule_id = r.id AND h.changed_by IS NOT NULL);

UPDATE detection_rule
   SET description = btrim(
           regexp_replace(description, '\s*\((policy [0-9-]+|§[^)]*)\)', '', 'g')),
       version = version + 1,
       updated_at = now()
 WHERE id IN (SELECT id FROM reworded);

INSERT INTO detection_rule_history (rule_id, version, change_type, snapshot, changed_by, changed_at)
SELECT id, version, 'UPDATE',
       jsonb_build_object(
           'id', id, 'name', name, 'description', description, 'rule_type', rule_type,
           'access_path', access_path, 'severity', severity, 'enabled', enabled,
           'auto_request', auto_request, 'condition', condition, 'aggregate', aggregate,
           'group_by', group_by, 'version', version),
       NULL, now()
  FROM detection_rule
 WHERE id IN (SELECT id FROM reworded);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    pass  # 문구 정리 — 이전 문장은 룰 변경 이력에 남아 있다
