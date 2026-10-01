"""해시체인 검증 명령어 — 종료 코드와 출력 (M6)"""

import pytest
from sqlalchemy import text

from app.ledger.append import append_access_logs
from app.scripts import verify_chain as script

from conftest import make_entry


@pytest.fixture
def app_account_env(monkeypatch, settings):
    """명령어는 운영처럼 환경변수(ARGUS_DB_*)로 앱 계정 접속 정보를 받는다"""
    monkeypatch.setenv("ARGUS_DB_HOST", settings.db_host)
    monkeypatch.setenv("ARGUS_DB_PORT", str(settings.db_port))
    monkeypatch.setenv("ARGUS_DB_NAME", settings.db_name)
    monkeypatch.setenv("ARGUS_DB_USER", settings.db_user)
    monkeypatch.setenv("ARGUS_DB_PASSWORD", settings.db_password.get_secret_value())


def _append(engine, n: int) -> list[int]:
    with engine.begin() as conn:
        return append_access_logs(conn, [make_entry() for _ in range(n)]).inserted_ids


def test_ok_exits_zero(app_engine, app_account_env, capsys):
    _append(app_engine, 3)

    assert script.main() == 0
    assert "OK: 해시체인 정상 — 3건 확인" in capsys.readouterr().out


def test_empty_ledger_is_ok(app_account_env, capsys):
    assert script.main() == 0
    assert "0건 확인" in capsys.readouterr().out


def test_tampered_exits_one_with_location(admin_engine, app_engine, app_account_env, capsys):
    ids = _append(app_engine, 3)
    with admin_engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE access_log DISABLE TRIGGER trg_access_log_no_update")
        conn.execute(
            text("UPDATE access_log SET subject_count = 999 WHERE id = :id"), {"id": ids[2]}
        )
        conn.exec_driver_sql("ALTER TABLE access_log ENABLE TRIGGER trg_access_log_no_update")

    assert script.main() == 1
    err = capsys.readouterr().err
    assert f"id={ids[2]}" in err and "tampered" in err and "그 앞 2건은 정상" in err


def test_db_error_exits_two_without_password(app_account_env, monkeypatch, capsys):
    monkeypatch.setenv("ARGUS_DB_PASSWORD", "wrong-password-for-test")

    assert script.main() == 2
    err = capsys.readouterr().err
    assert err.startswith("오류:") and "wrong-password-for-test" not in err
