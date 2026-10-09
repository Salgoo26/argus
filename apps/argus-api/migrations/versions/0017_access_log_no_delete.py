"""§8③ 위·변조 방지 — access_log DELETE·TRUNCATE도 트리거로 거부 (v0.1 보강 I, 갭 A16)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-10

0002는 UPDATE만 트리거로 막고, 삭제는 권한(앱 롤에 DELETE·TRUNCATE 없음)으로만 막았다.
테이블 소유자(마이그레이션 계정)는 권한과 무관하게 지울 수 있어서, 끝부분 기록을 지우면 남은
해시체인이 그대로 이어져 검증을 통과한다. 이제 소유자를 포함해 누구든 행 삭제·TRUNCATE가 거부된다.

- 행 트리거(BEFORE DELETE FOR EACH ROW) + 문장 트리거(BEFORE TRUNCATE FOR EACH STATEMENT —
  TRUNCATE는 행 트리거를 거치지 않는다)
- **파기 배치(v0.2)를 만들 때 재설계한다**: 보관기간(1년)이 지난 기록의 파기는 지금 막힌다.
  그때 파기 전용 경로(파기 롤만 통과 + destruction_history에 체인 앵커 기록)로 바꾼다
- 한계: 소유자·슈퍼유저는 트리거를 끌 수 있다(ALTER TABLE ... DISABLE TRIGGER). 그래서 점검 보고서에
  원장 마지막 id·해시를 남기고, 다음 보고서에서 그 행이 그대로인지 확인한다(app/reports/summary.py)
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE FUNCTION forbid_access_log_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'access_log is append-only (% blocked)', TG_OP; END $$;

CREATE TRIGGER trg_access_log_no_delete BEFORE DELETE ON access_log
    FOR EACH ROW EXECUTE FUNCTION forbid_access_log_delete();
CREATE TRIGGER trg_access_log_no_truncate BEFORE TRUNCATE ON access_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_access_log_delete();
"""

DOWNGRADE_SQL = """
DROP TRIGGER trg_access_log_no_truncate ON access_log;
DROP TRIGGER trg_access_log_no_delete ON access_log;
DROP FUNCTION forbid_access_log_delete();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
