"""마이그레이션 — [S] 테이블 생성, upgrade/downgrade 왕복"""

from alembic import command
from sqlalchemy import create_engine, inspect, text

from conftest import alembic_config, create_database, drop_database, requires_db


def test_skeleton_tables_created(engine):
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == {
        "operator",
        "member",
        "outbox",
        "consent_item",  # 고객 화면 (0002)
        "member_consent",
        "product",  # 주문·결제 (0003)
        "orders",
        "payment",
        "refund_account",
        "retained_member_record",
        "destruction_history",
        "inquiry",  # 1:1 문의 (0004)
        "db_access_token",  # DB 접속 토큰 발급 기록 (0005)
        "shipping_address",  # 배송지 (0007)
        "operator_permission_history",  # 권한 이력 (0009, v0.1 보강 L-3)
    }


def test_outbox_pending_partial_index_exists(engine):
    # relay가 PENDING만 빠르게 집어 가는 부분 인덱스 — WHERE 절까지 옮겨졌는지
    with engine.connect() as conn:
        indexdef = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_outbox_pending'")
        ).scalar_one()
    assert "WHERE" in indexdef and "PENDING" in indexdef


@requires_db
def test_upgrade_downgrade_roundtrip():
    name, url = create_database("platform_mig")
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
