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
        "order_claim",  # 취소·반품(청약철회) 기록 (0011)
        "order_status_history",  # 주문 상태 이력 (0011)
        "bank_deposit",  # 무통장입금 (0014)
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


@requires_db
def test_migration_moves_operators_to_three_departments():
    """0015 — 소속 OPS·CS → 커머스운영팀, ADMIN 역할 → IT팀. 역할은 그대로, 이력·명부 동기화"""
    name, url = create_database("platform_mig_dept")
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "0014")
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO operator (login_id, password_hash, name, team, role,"
                    " employment_status, terminated_at) VALUES"
                    " ('ops_a', 'x', '가상운영', 'OPS', 'OPS', 'ACTIVE', NULL),"
                    " ('cs_a', 'x', '가상상담', 'CS', 'CS', 'ACTIVE', NULL),"
                    " ('mkt_a', 'x', '가상마케팅', 'MARKETING', 'MARKETING', 'ACTIVE', NULL),"
                    " ('adm_a', 'x', '가상관리', 'OPS', 'ADMIN', 'ACTIVE', NULL),"
                    " ('cs_old', 'x', '가상퇴직', 'CS', 'CS', 'TERMINATED',"
                    "  '2026-10-01T09:00:00+09:00')"
                )
            )
        command.upgrade(cfg, "0015")
        with engine.connect() as conn:
            teams = dict(conn.execute(text("SELECT login_id, team FROM operator")).all())
            roles = dict(conn.execute(text("SELECT login_id, role FROM operator")).all())
            history = (
                conn.execute(
                    text(
                        "SELECT o.login_id, h.change_type, h.before_team, h.after_team,"
                        " h.before_role, h.after_role, h.after_status, h.actor_id, h.reason"
                        " FROM operator_permission_history h"
                        " JOIN operator o ON o.id = h.operator_id"
                    )
                )
                .mappings()
                .all()
            )
            events = conn.execute(
                text("SELECT event_id, payload FROM outbox WHERE topic = 'HANDLER'")
            ).all()
        assert teams == {
            "ops_a": "COMMERCE_OPS",
            "cs_a": "COMMERCE_OPS",
            "mkt_a": "MARKETING",
            "adm_a": "IT",
            "cs_old": "COMMERCE_OPS",
        }
        assert roles == {
            "ops_a": "OPS",
            "cs_a": "CS",
            "mkt_a": "MARKETING",
            "adm_a": "ADMIN",
            "cs_old": "CS",
        }
        # 바뀐 계정만(마케팅은 그대로) 이력 1행씩 — 처리자 시스템, 사유 필수
        assert {h["login_id"]: (h["before_team"], h["after_team"]) for h in history} == {
            "ops_a": ("OPS", "COMMERCE_OPS"),
            "cs_a": ("CS", "COMMERCE_OPS"),
            "adm_a": ("OPS", "IT"),
            "cs_old": ("CS", "COMMERCE_OPS"),
        }
        assert all(h["change_type"] == "CHANGE" and h["actor_id"] is None for h in history)
        assert all(h["before_role"] == h["after_role"] and h["reason"] for h in history)
        assert {h["login_id"]: h["after_status"] for h in history}["cs_old"] == "TERMINATED"
        # Argus 명부도 평소 경로로 — 취급자 상태 전체, 권한(role)은 보내지 않는다
        by_login = {p["handler"]["login_id"]: (str(e), p) for e, p in events}
        assert set(by_login) == {"ops_a", "cs_a", "adm_a", "cs_old"}
        event_id, adm = by_login["adm_a"]
        assert adm["type"] == "HANDLER_UPDATED" and adm["event_id"] == event_id
        assert adm["handler"] == {
            "login_id": "adm_a",
            "name": "가상관리",
            "team": "IT",
            "employment_status": "ACTIVE",
        }
        old = by_login["cs_old"][1]["handler"]
        assert old["employment_status"] == "TERMINATED" and old["terminated_at"]

        command.downgrade(cfg, "0014")
        with engine.connect() as conn:
            back = dict(conn.execute(text("SELECT login_id, team FROM operator")).all())
        assert back == {
            "ops_a": "OPS",
            "cs_a": "OPS",
            "mkt_a": "MARKETING",
            "adm_a": "OPS",
            "cs_old": "OPS",
        }
        engine.dispose()
    finally:
        drop_database(name)
