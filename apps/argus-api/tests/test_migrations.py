"""마이그레이션 — [S] 테이블 생성, 기준 데이터, upgrade/downgrade 왕복"""

from alembic import command
from sqlalchemy import create_engine, inspect, text

from conftest import alembic_config, create_database, drop_database, requires_db

SKELETON_TABLES = {
    "source_system",
    "handler",
    "argus_user",
    "access_log",
    "detection_rule",
    "detection",
    "detection_log",
    "detection_status_history",
    "explanation",
    "detection_batch_run",
    "setting",
}


def test_skeleton_tables_created(admin_engine):
    tables = set(inspect(admin_engine).get_table_names()) - {"alembic_version"}
    assert tables == SKELETON_TABLES


def test_bulk_download_rule_seeded(admin_engine):
    # M3 — Walking Skeleton의 유일한 룰 (db-schema 3-6 [S])
    with admin_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rule_type, access_path, severity, enabled, condition FROM detection_rule")
        ).one()
    assert row[:4] == ("EVENT", "APP", "HIGH", True)
    assert row[4]["all"][0] == {"field": "action", "op": "eq", "value": "DOWNLOAD"}


def test_reference_data_seeded(admin_engine):
    with admin_engine.connect() as conn:
        codes = set(conn.execute(text("SELECT code FROM source_system")).scalars())
        interval = conn.execute(
            text("SELECT value FROM setting WHERE key = 'detection_interval_min'")
        ).scalar_one()
    assert codes == {"PLATFORM", "ARGUS"}
    assert interval == 5


def test_open_detection_partial_unique_index_exists(admin_engine):
    # 진행 중 탐지건 1개 제약 (policy 2-3) — 부분 인덱스라 WHERE 절까지 옮겨졌는지 확인
    with admin_engine.connect() as conn:
        indexdef = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ux_detection_open_group'")
        ).scalar_one()
    assert "UNIQUE" in indexdef and "WHERE" in indexdef


@requires_db
def test_upgrade_downgrade_roundtrip(test_db):
    # 세션 DB와 별개의 DB에서 왕복 — 세션 DB가 argus_app 롤을 쓰고 있으므로 롤은 보존된다
    name, url = create_database("argus_mig")
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        engine = create_engine(url)
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
        engine.dispose()
        command.upgrade(cfg, "head")
    finally:
        drop_database(name)
