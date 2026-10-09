"""Argus 계정 관리 스크립트 — 담당자 생성, 비밀번호 설정, 잠금 해제"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select, text, update

from app.auth.passwords import unusable_password_hash, verify_password
from app.models import argus_user, argus_user_history, handler
from app.scripts import users

NEW_PASSWORD = "brand-new-password-1234"  # 테스트 전용 더미 값


@pytest.fixture(autouse=True)
def script_env(monkeypatch, test_db):
    # 스크립트는 운영처럼 앱 계정으로 접속한다
    monkeypatch.setenv("ARGUS_DB_HOST", test_db.admin_url.host)
    monkeypatch.setenv("ARGUS_DB_PORT", str(test_db.admin_url.port or 5432))
    monkeypatch.setenv("ARGUS_DB_NAME", test_db.name)
    monkeypatch.setenv("ARGUS_DB_USER", test_db.app_login)
    monkeypatch.setenv("ARGUS_DB_PASSWORD", test_db.app_password)
    monkeypatch.setenv("TEST_NEW_PASSWORD", NEW_PASSWORD)


@pytest.fixture(autouse=True)
def clean_users(admin_engine):
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))


def run(*argv, reason: str | None = "테스트 — 담당자 지정") -> int:
    extra = ["--reason", reason] if reason is not None else []
    return users.main([*argv, "--password-env", "TEST_NEW_PASSWORD", *extra])


def row(admin_engine, login_id) -> dict:
    with admin_engine.connect() as conn:
        return dict(
            conn.execute(select(argus_user).where(argus_user.c.login_id == login_id))
            .mappings()
            .one()
        )


def add_synced_handler(admin_engine, login_id="ops_park", status="ACTIVE") -> None:
    with admin_engine.begin() as conn:
        handler_id = conn.execute(
            insert(handler)
            .values(
                source_system_id=1,
                login_id=login_id,
                name="박지훈",
                team="OPS",
                employment_status="ACTIVE",
                last_event_at=datetime.now(UTC),
            )
            .returning(handler.c.id)
        ).scalar_one()
        conn.execute(
            insert(argus_user).values(
                login_id=login_id,
                password_hash=unusable_password_hash(),
                role="HANDLER",
                handler_id=handler_id,
                status=status,
            )
        )


def test_create_officer(admin_engine):
    assert run("create-officer", "officer") == 0
    created = row(admin_engine, "officer")
    assert created["role"] == "OFFICER" and created["status"] == "ACTIVE"
    assert verify_password(created["password_hash"], NEW_PASSWORD)
    assert created["password_hash"].startswith("$argon2id$")


def test_create_duplicate_officer_fails(admin_engine):
    run("create-officer", "officer")
    assert run("create-officer", "officer") == 1


def test_set_password_for_synced_handler(admin_engine):
    add_synced_handler(admin_engine)
    assert run("set-password", "ops_park") == 0
    assert verify_password(row(admin_engine, "ops_park")["password_hash"], NEW_PASSWORD)


def test_set_password_does_not_revive_disabled_account(admin_engine):
    add_synced_handler(admin_engine, status="DISABLED")
    run("set-password", "ops_park")
    assert row(admin_engine, "ops_park")["status"] == "DISABLED"


def test_unlock_only_locked_accounts(admin_engine):
    add_synced_handler(admin_engine)
    with admin_engine.begin() as conn:
        conn.execute(update(argus_user).values(status="LOCKED", failed_login_count=5))
    assert users.main(["unlock", "ops_park", "--reason", "본인 확인"]) == 0
    unlocked = row(admin_engine, "ops_park")
    assert (unlocked["status"], unlocked["failed_login_count"]) == ("ACTIVE", 0)

    with admin_engine.begin() as conn:
        conn.execute(update(argus_user).values(status="DISABLED"))
    assert (
        users.main(["unlock", "ops_park", "--reason", "x"]) == 1
    )  # 퇴직 등 비활성은 해제 대상 아님


@pytest.mark.parametrize("password", ["short", ""])
def test_short_password_is_refused(admin_engine, monkeypatch, password):
    monkeypatch.setenv("TEST_NEW_PASSWORD", password)
    assert run("create-officer", "officer") == 1


def test_unknown_account(admin_engine):
    assert run("set-password", "nobody") == 1
    assert users.main(["unlock", "nobody", "--reason", "x"]) == 1


# ── 플랫폼 아이디 = Argus 아이디 원칙 (2026-10-02) ─────────────────


def test_create_officer_promotes_unused_synced_handler_account(admin_engine):
    # 플랫폼 시드 → 동기화로 담당자 아이디의 A5 계정(로그인 불가)이 먼저 생긴 경우
    add_synced_handler(admin_engine, login_id="officer")
    synced = row(admin_engine, "officer")

    assert run("create-officer", "officer") == 0

    promoted = row(admin_engine, "officer")
    assert promoted["role"] == "OFFICER" and promoted["id"] == synced["id"]
    assert verify_password(promoted["password_hash"], NEW_PASSWORD)
    # 명부 연결은 남긴다 — 플랫폼 퇴직 동기화가 담당자 계정도 막도록
    assert promoted["handler_id"] == synced["handler_id"]


def test_handler_account_in_use_is_not_promoted(admin_engine):
    add_synced_handler(admin_engine, login_id="ops_park")
    with admin_engine.begin() as conn:
        conn.execute(
            update(argus_user)
            .where(argus_user.c.login_id == "ops_park")
            .values(last_login_at=datetime.now(UTC))
        )

    assert run("create-officer", "ops_park") == 1
    assert row(admin_engine, "ops_park")["role"] == "HANDLER"


def test_promotion_does_not_revive_disabled_account(admin_engine):
    add_synced_handler(admin_engine, login_id="officer", status="DISABLED")
    assert run("create-officer", "officer") == 0
    assert row(admin_engine, "officer")["status"] == "DISABLED"


# ── 계정 이력 (v0.1 보강 L-4) — 스크립트도 화면과 같은 이력을 남긴다 ──────


def history(admin_engine) -> list[dict]:
    with admin_engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(argus_user_history).order_by(argus_user_history.c.id)
            ).mappings()
        ]


@pytest.mark.parametrize("command", ["create-officer", "unlock"])
@pytest.mark.parametrize("reason", [None, "  "], ids=["missing", "blank"])
def test_permission_commands_need_a_reason(admin_engine, command, reason):
    add_synced_handler(admin_engine, status="LOCKED")
    assert run(command, "ops_park", reason=reason) == 1
    assert row(admin_engine, "ops_park")["role"] == "HANDLER"
    assert history(admin_engine) == []


def test_script_changes_are_recorded(admin_engine):
    add_synced_handler(admin_engine, login_id="officer")  # 동기화로 먼저 생긴 계정 → 전환
    add_synced_handler(admin_engine, login_id="ops_park", status="LOCKED")
    assert run("create-officer", "officer", reason="최초 담당자") == 0
    assert run("create-officer", "officer2", reason="두 번째 담당자") == 0
    assert run("unlock", "ops_park", reason="본인 확인 후 해제") == 0
    assert run("set-password", "ops_park", reason=None) == 0  # 권한 변경이 아니라 이력 없음

    rows = [
        (h["login_id"], h["change_type"], h["before_role"], h["after_role"], h["reason"])
        for h in history(admin_engine)
    ]
    assert rows == [
        ("officer", "GRANT", "HANDLER", "OFFICER", "최초 담당자"),
        ("officer2", "GRANT", None, "OFFICER", "두 번째 담당자"),
        ("ops_park", "UNLOCK", "HANDLER", "HANDLER", "본인 확인 후 해제"),
    ]
    assert all(h["actor_login_id"] is None for h in history(admin_engine))  # 운영 스크립트
