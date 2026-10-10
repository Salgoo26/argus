"""Argus 계정 관리 + 계정 이력 (v0.1 보강 L-4 — 고시 §5①③, 안내서 61~62)

부여(취급자 → 담당자) / 변경(담당자 → 취급자) / 말소(비활성화·재활성화) / 잠금 해제.
모든 변경에 사유 필수, 본인 계정 불가, 이력은 append-only, Argus 자체 접속기록에서 제외.
"""

import json
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from app.auth.passwords import hash_password
from app.models import access_log, argus_user, argus_user_history, handler

from conftest import TEST_CLIENT_ADDR, signed_headers

PASSWORD = "users-test-password-1"  # 테스트 전용 더미 값
USERS = "/api/users"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash):
    """담당자 officer(연결 없음)·kim_sec(명부 sec_kim에 연결), 취급자 ops_park (가상 인물)"""
    with admin_engine.begin() as conn:

        def add_handler(login_id: str, name: str) -> int:
            return conn.execute(
                insert(handler)
                .values(
                    source_system_id=1,
                    login_id=login_id,
                    name=name,
                    team="OPS",
                    employment_status="ACTIVE",
                    last_event_at=datetime.now(UTC),
                )
                .returning(handler.c.id)
            ).scalar_one()

        conn.execute(
            insert(argus_user).values(
                login_id="officer", password_hash=password_hash, role="OFFICER"
            )
        )
        conn.execute(
            insert(argus_user).values(
                login_id="kim_sec",
                password_hash=password_hash,
                role="OFFICER",
                handler_id=add_handler("sec_kim", "김보안"),
            )
        )
        conn.execute(
            insert(argus_user).values(
                login_id="ops_park",
                password_hash=password_hash,
                role="HANDLER",
                handler_id=add_handler("ops_park", "박지훈"),
            )
        )
    yield
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM push_subscription"))
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))


@pytest.fixture
def as_user(app):
    clients = []

    def login(login_id: str) -> TestClient:
        c = TestClient(app, client=TEST_CLIENT_ADDR)
        clients.append(c)
        res = c.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD})
        assert res.status_code == 200, res.text
        return c

    yield login
    for c in clients:
        c.close()


def user_id(app_engine, login_id: str) -> int:
    with app_engine.connect() as conn:
        return conn.execute(
            select(argus_user.c.id).where(argus_user.c.login_id == login_id)
        ).scalar_one()


def account(app_engine, login_id: str) -> dict:
    with app_engine.connect() as conn:
        return dict(
            conn.execute(select(argus_user).where(argus_user.c.login_id == login_id))
            .mappings()
            .one()
        )


def history(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(argus_user_history).order_by(argus_user_history.c.id)
            ).mappings()
        ]


def act(client, uid: int, action: str, reason: str | None = "업무 변경 — 결재 2026-10-10-02"):
    body = {} if reason is None else {"reason": reason}
    return client.post(f"{USERS}/{uid}/{action}", json=body)


# ── 목록·권한 ─────────────────────────────────────────────


def test_officer_lists_accounts_with_roster_link(as_user):
    items = {u["login_id"]: u for u in as_user("officer").get(USERS).json()["items"]}
    assert items["ops_park"]["role"] == "HANDLER"
    assert items["ops_park"]["handler"] == {
        "login_id": "ops_park",
        "name": "박지훈",
        "employment_status": "ACTIVE",
    }
    assert items["officer"]["handler"] is None
    assert "password_hash" not in str(items)


def test_handler_cannot_manage_accounts(app_engine, as_user):
    handler_client = as_user("ops_park")
    assert handler_client.get(USERS).status_code == 403
    assert act(handler_client, user_id(app_engine, "officer"), "disable").status_code == 403
    assert handler_client.post(f"{USERS}/history/search", json={}).status_code == 403


def test_cannot_change_own_account(app_engine, as_user):
    me = user_id(app_engine, "kim_sec")
    for action in ("demote", "disable"):
        res = act(as_user("kim_sec"), me, action)
        assert res.status_code == 403 and res.json()["error"]["code"] == "SELF_CHANGE_FORBIDDEN"
    assert history(app_engine) == []


@pytest.mark.parametrize("reason", [None, "", "   "], ids=["missing", "empty", "blank"])
@pytest.mark.parametrize("action", ["promote", "demote", "disable", "enable", "unlock"])
def test_every_change_needs_a_reason(app_engine, as_user, action, reason):
    target = user_id(app_engine, "kim_sec" if action == "demote" else "ops_park")
    assert act(as_user("officer"), target, action, reason).status_code == 400
    assert history(app_engine) == []


# ── 부여·변경·말소·해제 ───────────────────────────────────


def test_promote_handler_to_officer(app_engine, as_user):
    officer = as_user("officer")
    res = act(officer, user_id(app_engine, "ops_park"), "promote")
    assert res.status_code == 200 and res.json()["role"] == "OFFICER"
    [grant] = history(app_engine)
    assert (grant["change_type"], grant["before_role"], grant["after_role"]) == (
        "GRANT",
        "HANDLER",
        "OFFICER",
    )
    assert (grant["login_id"], grant["actor_login_id"]) == ("ops_park", "officer")
    assert grant["reason"] == "업무 변경 — 결재 2026-10-10-02"
    # 이미 담당자면 다시 부여할 수 없다
    assert act(officer, user_id(app_engine, "ops_park"), "promote").status_code == 409


def test_demote_needs_a_roster_link(app_engine, as_user):
    officer = as_user("officer")
    res = act(officer, user_id(app_engine, "kim_sec"), "demote")
    assert res.status_code == 200 and res.json()["role"] == "HANDLER"
    assert history(app_engine)[0]["change_type"] == "CHANGE"

    assert act(officer, 999_999, "promote").status_code == 404  # 없는 계정


def test_officer_without_roster_link_cannot_be_demoted(app_engine, as_user):
    res = act(as_user("kim_sec"), user_id(app_engine, "officer"), "demote")
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_HANDLER_LINK"


def test_disable_and_enable(app_engine, as_user):
    officer = as_user("officer")
    target = user_id(app_engine, "ops_park")
    handler_client = as_user("ops_park")

    assert act(officer, target, "disable").json()["status"] == "DISABLED"
    assert handler_client.get("/api/auth/me").status_code == 401  # 로그인해 있던 세션도 막힌다
    assert act(officer, target, "disable").status_code == 409

    assert act(officer, target, "enable").json()["status"] == "ACTIVE"
    assert [
        (h["change_type"], h["before_status"], h["after_status"]) for h in history(app_engine)
    ] == [
        ("REVOKE", "ACTIVE", "DISABLED"),
        ("RESTORE", "DISABLED", "ACTIVE"),
    ]
    as_user("ops_park")  # 다시 로그인된다


def test_terminated_handler_cannot_be_re_enabled(app_engine, admin_engine, as_user):
    officer = as_user("officer")
    target = user_id(app_engine, "ops_park")
    act(officer, target, "disable")
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE handler SET employment_status = 'TERMINATED', terminated_at = now()"
                " WHERE login_id = 'ops_park'"
            )
        )
    res = act(officer, target, "enable")
    assert res.status_code == 409 and res.json()["error"]["code"] == "HANDLER_TERMINATED"


def test_unlock_locked_account(app, app_engine, as_user):
    with TestClient(app, client=TEST_CLIENT_ADDR) as c:
        for _ in range(5):
            c.post("/api/auth/login", json={"login_id": "ops_park", "password": "wrong-password"})
    assert account(app_engine, "ops_park")["status"] == "LOCKED"

    officer = as_user("officer")
    target = user_id(app_engine, "ops_park")
    assert act(officer, target, "unlock").json()["status"] == "ACTIVE"
    assert account(app_engine, "ops_park")["failed_login_count"] == 0
    assert history(app_engine)[-1]["change_type"] == "UNLOCK"
    assert act(officer, target, "unlock").json()["error"]["code"] == "NOT_LOCKED"


# ── 이력 ─────────────────────────────────────────────────


def test_history_search_by_target_and_period(app_engine, as_user):
    officer = as_user("officer")
    act(officer, user_id(app_engine, "ops_park"), "promote")
    act(officer, user_id(app_engine, "kim_sec"), "disable")

    everything = officer.post(f"{USERS}/history/search", json={}).json()
    assert everything["total"] == 2
    assert [i["login_id"] for i in everything["items"]] == ["kim_sec", "ops_park"]  # 최신 먼저
    only = officer.post(f"{USERS}/history/search", json={"login_id": "ops_park"}).json()
    assert only["total"] == 1
    past = officer.post(
        f"{USERS}/history/search", json={"date_from": "2020-01-01", "date_to": "2020-01-31"}
    ).json()
    assert past["total"] == 0


@pytest.mark.parametrize(
    "statement",
    ["UPDATE argus_user_history SET reason = '고침'", "DELETE FROM argus_user_history"],
)
def test_history_is_append_only_even_for_owner(app_engine, admin_engine, as_user, statement):
    act(as_user("officer"), user_id(app_engine, "ops_park"), "promote")
    with pytest.raises(DBAPIError, match="append-only"):
        with admin_engine.begin() as conn:
            conn.execute(text(statement))
    with pytest.raises(DBAPIError, match="permission denied"):  # 앱 계정은 권한부터 없다
        with app_engine.begin() as conn:
            conn.execute(text(statement))


def test_account_screens_stay_out_of_argus_self_log(app_engine, as_user):
    officer = as_user("officer")
    officer.get(USERS)
    act(officer, user_id(app_engine, "ops_park"), "promote")
    officer.post(f"{USERS}/history/search", json={})
    with app_engine.connect() as conn:
        paths = list(conn.execute(select(access_log.c.request_path)).scalars())
    assert not [p for p in paths if p and p.startswith(USERS)]


# ── 명부 동기화도 이력에 (처리자 = 시스템) ─────────────────


def _sync(client, type_: str, login_id: str, status: str = "ACTIVE") -> None:
    fields = {"login_id": login_id, "name": "가상동기", "team": "CS", "employment_status": status}
    if status == "TERMINATED":
        fields["terminated_at"] = datetime.now(UTC).isoformat()
    event = {
        "event_id": str(uuid.uuid4()),
        "type": type_,
        "occurred_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "handler": fields,
    }
    body = json.dumps({"events": [event]}).encode()
    res = client.post("/ingest/v1/handler-events", content=body, headers=signed_headers(body))
    assert res.status_code == 200 and res.json()["accepted"] == 1, res.text
    time.sleep(0.001)  # 같은 사람의 다음 이벤트가 더 늦은 시각이 되게


def test_roster_sync_records_grant_and_revoke(client, app_engine):
    _sync(client, "HANDLER_CREATED", "cs_sync")
    _sync(client, "HANDLER_TERMINATED", "cs_sync", "TERMINATED")
    rows = [h for h in history(app_engine) if h["login_id"] == "cs_sync"]
    assert [(h["change_type"], h["after_role"], h["after_status"]) for h in rows] == [
        ("GRANT", "HANDLER", "ACTIVE"),
        ("REVOKE", "HANDLER", "DISABLED"),
    ]
    assert all(h["actor_login_id"] is None for h in rows)  # 시스템(명부 동기화)
    assert [h["reason"] for h in rows] == [
        "취급자 명부 동기화 — 플랫폼 계정 생성",
        "취급자 명부 동기화 — 플랫폼 퇴직",
    ]
