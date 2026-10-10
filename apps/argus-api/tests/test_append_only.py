"""§8③ append-only 이중 장치 — DB 권한(앱 계정) + 트리거(누구든)"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.ledger.append import append_access_logs

from conftest import make_entry


@pytest.fixture
def one_row(app_engine):
    with app_engine.begin() as conn:
        append_access_logs(conn, [make_entry()])


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE access_log SET actor_login_id = 'someone_else'",
        "DELETE FROM access_log",
        "TRUNCATE access_log",
    ],
)
def test_app_role_cannot_modify_ledger(app_engine, one_row, statement):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.exec_driver_sql(statement)


def test_trigger_blocks_update_even_for_owner(admin_engine, one_row):
    # 권한 설정이 실수로 풀려도(소유자·슈퍼유저) UPDATE는 트리거가 막는다
    with pytest.raises(ProgrammingError, match="append-only"):
        with admin_engine.begin() as conn:
            conn.exec_driver_sql("UPDATE access_log SET actor_login_id = 'someone_else'")


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM access_log",
        "DELETE FROM access_log WHERE id = (SELECT max(id) FROM access_log)",  # 끝부분 삭제
        "TRUNCATE access_log RESTART IDENTITY CASCADE",
    ],
)
def test_trigger_blocks_delete_and_truncate_even_for_owner(admin_engine, one_row, statement):
    # v0.1 보강 I (갭 A16) — 소유자는 권한과 무관하게 지울 수 있었다. 이제 트리거가 막는다
    with pytest.raises(ProgrammingError, match="append-only"):
        with admin_engine.begin() as conn:
            conn.exec_driver_sql(statement)
    with admin_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM access_log").scalar_one() == 1


def test_app_role_cannot_write_reference_data(app_engine):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO source_system (code, name) VALUES ('X', 'x')")


def test_app_login_is_not_owner_nor_superuser(app_engine):
    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.rolsuper, t.tableowner = current_user AS is_owner "
                "FROM pg_roles r, pg_tables t "
                "WHERE r.rolname = current_user AND t.tablename = 'access_log'"
            )
        ).one()
        is_member = conn.execute(
            text("SELECT pg_has_role(current_user, 'argus_app', 'MEMBER')")
        ).scalar_one()
    assert row.rolsuper is False
    assert row.is_owner is False
    assert is_member is True
