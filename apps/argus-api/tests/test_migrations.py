"""마이그레이션 — [S] 테이블 생성, 기준 데이터, upgrade/downgrade 왕복"""

from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.detection.rules import validate_rule

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
    "detection_rule_history",  # 기능 레이어 6 (0008)
    "explanation_attachment",  # 기능 레이어 7 ③ (0010)
    "inspection_report",  # 기능 레이어 9 (0013)
    "notification",  # v0.1 보강 F (0015)
    "push_subscription",  # v0.1 보강 F-4 (0016)
    "argus_user_history",  # v0.1 보강 L-4 (0020)
    # v0.1 보강 N (0021) — 보호 대상 등록부·이력, DB 구조 목록·수신 기록
    "protected_column",
    "protected_column_history",
    "db_schema_column",
    "db_schema_receipt",
}


def test_skeleton_tables_created(admin_engine):
    tables = set(inspect(admin_engine).get_table_names()) - {"alembic_version"}
    assert tables == SKELETON_TABLES


def test_default_rules_seeded(admin_engine, seed_rules):
    # 대량 다운로드(M3, [S]) + 야간·주말·퇴직자(기능 레이어 1) + 대량 조회·급증(4)
    # + 결제수단 조회(7) + DB 직접 야간·주말·전월 대비 급증(8 ③)
    # 다른 테스트가 켜고 끈 상태가 아니라 마이그레이션 직후 상태(seed_rules)를 본다
    rows = {
        r["name"]: (r["rule_type"], r["access_path"], r["severity"], r["enabled"])
        for r in seed_rules
    }
    assert rows == {
        "대량 다운로드": ("EVENT", "APP", "HIGH", True),
        "야간 접속": ("EVENT", "APP", "MEDIUM", True),
        "주말 접속": ("EVENT", "APP", "LOW", True),
        "퇴직자 계정 접속": ("EVENT", "APP", "HIGH", True),
        "대량 조회": ("AGGREGATE", "APP", "HIGH", True),
        "전월 대비 급증": ("AGGREGATE", "APP", "MEDIUM", True),
        "결제수단 조회": ("EVENT", "APP", "HIGH", True),
        # DB 직접 접근(2티어) 기본 룰 — 같은 이름의 3티어 룰보다 한 단계 높게 (0012)
        "DB 직접 야간 접근": ("EVENT", "DB", "HIGH", True),
        "DB 직접 주말 접근": ("EVENT", "DB", "MEDIUM", True),
        "DB 직접 전월 대비 급증": ("AGGREGATE", "DB", "MEDIUM", True),
        # v0.1 보강 K-2 — 접속지·특정 회원 기반 (0019)
        "허용 범위 밖 접속지": ("EVENT", "ALL", "HIGH", True),
        "짧은 시간 여러 접속지": ("AGGREGATE", "ALL", "MEDIUM", True),
        "특정 회원 반복 처리": ("AGGREGATE", "APP", "MEDIUM", True),
    }
    bulk = next(r for r in seed_rules if r["name"] == "대량 다운로드")
    assert bulk["condition"]["all"][0] == {"field": "action", "op": "eq", "value": "DOWNLOAD"}


def test_seeded_rules_are_evaluable(seed_rules):
    # 시드 룰이 해석 불가면 순찰 전체가 멈춘다(db-schema 3-7 #2) — 배포 전에 여기서 잡는다
    for rule in seed_rules:
        validate_rule(rule)


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
