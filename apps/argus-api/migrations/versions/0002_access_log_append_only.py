"""§8③ 위·변조 방지 — access_log append-only 이중 장치 + DB 권한 분리

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29

- 트리거: UPDATE 시도는 누가 하든 예외 (권한 설정 실수 대비)
- 권한: 앱 롤(argus_app)은 access_log에 SELECT·INSERT만, 파기 롤(argus_purge)만 DELETE.
  UPDATE는 아무 롤에도 주지 않는다.
- 두 롤은 NOLOGIN이다. 실제 로그인 계정은 app.scripts.provision_db_roles가 만들어
  argus_app 롤에 가입시킨다(비밀번호가 마이그레이션 코드에 들어가지 않게).
- 앱은 테이블 소유자로 접속하지 않는다. 소유자는 GRANT와 무관하게 모든 권한을 갖기 때문이다.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# 테이블을 추가하는 이후 마이그레이션은 여기처럼 권한을 명시적으로 부여한다
# (ALTER DEFAULT PRIVILEGES로 일괄 부여하지 않음 — 테이블마다 최소 권한을 판단하기 위해).
UPGRADE_SQL = """
CREATE FUNCTION forbid_access_log_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'access_log is append-only'; END $$;
CREATE TRIGGER trg_access_log_no_update BEFORE UPDATE ON access_log
    FOR EACH ROW EXECUTE FUNCTION forbid_access_log_update();

-- 롤은 클러스터 전역 객체라 이미 있을 수 있다 (같은 인스턴스의 다른 DB, 재적용)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'argus_app') THEN
        CREATE ROLE argus_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'argus_purge') THEN
        CREATE ROLE argus_purge NOLOGIN;
    END IF;
END $$;

-- 원장: 추가·조회만 (UPDATE·DELETE·TRUNCATE 없음)
GRANT SELECT, INSERT ON access_log TO argus_app;
GRANT SELECT, DELETE ON access_log TO argus_purge;       -- 파기 배치 전용 (Skeleton 이후)

-- 감사 추적 테이블: 추가·조회만
GRANT SELECT, INSERT ON detection_status_history TO argus_app;
GRANT SELECT, INSERT ON detection_log TO argus_app;

-- 기준 데이터: 조회만 (출처 시스템 추가는 마이그레이션으로)
GRANT SELECT ON source_system TO argus_app;

-- 업무 테이블: 삭제 없음
GRANT SELECT, INSERT, UPDATE ON
    handler, argus_user, detection_rule, detection, explanation, detection_batch_run, setting
    TO argus_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO argus_app;
"""

DOWNGRADE_SQL = """
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM argus_app, argus_purge;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM argus_app, argus_purge;

DROP TRIGGER trg_access_log_no_update ON access_log;
DROP FUNCTION forbid_access_log_update();

-- 같은 인스턴스의 다른 DB가 아직 쓰고 있으면 롤은 남겨 둔다
DO $$
BEGIN
    DROP ROLE IF EXISTS argus_purge;
    DROP ROLE IF EXISTS argus_app;
EXCEPTION WHEN dependent_objects_still_exist THEN
    RAISE NOTICE 'argus_app/argus_purge still used elsewhere — kept';
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
