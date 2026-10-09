"""관리자 계정·권한 관리 + 권한 이력 (v0.1 보강 L-2·L-3 — 고시 §5①③, 안내서 61~62)"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import operator, operator_permission_history

from conftest import TEST_CLIENT_ADDR, add_operator, login, outbox_payloads

ACCOUNTS = "/admin/accounts"
NEW_PASSWORD = "changed-pass-2026!"  # 테스트 전용 더미 값


@pytest.fixture
def admin(client, engine, password_hash):
    add_operator(engine, password_hash, login_id="admin_han", name="한도윤", role="ADMIN")
    assert login(client, "admin_han").status_code == 200
    return client


@pytest.fixture
def ops_id(engine, password_hash) -> int:
    return add_operator(engine, password_hash)  # ops_park (OPS)


def history(engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(operator_permission_history).order_by(operator_permission_history.c.id)
            ).mappings()
        ]


def create(admin, **overrides):
    body = {
        "login_id": "cs_new",
        "name": "신입상담",  # 가상 인물
        "team": "CS",
        "role": "CS",
        "reason": "CS팀 신규 입사 — 결재 2026-10-10-01",
    }
    return admin.post(ACCOUNTS, json=body | overrides)


# ── 부여 ──────────────────────────────────────────────────


def test_create_shows_temporary_password_once_and_records_grant(admin, engine):
    res = create(admin)
    assert res.status_code == 201, res.text
    body = res.json()
    temp = body["temporary_password"]
    assert len(temp) >= 12 and body["must_change_password"] is True

    [grant] = history(engine)
    assert grant["change_type"] == "GRANT"
    assert (grant["after_role"], grant["after_team"], grant["after_status"]) == (
        "CS",
        "CS",
        "ACTIVE",
    )
    assert grant["reason"] == "CS팀 신규 입사 — 결재 2026-10-10-01"
    assert grant["before_role"] is None and grant["actor_id"] is not None

    # 다시 보여 주지 않는다 — 목록에도, DB에도 평문이 없다
    listed = admin.get(ACCOUNTS).json()["items"]
    assert temp not in str(listed)
    with engine.connect() as conn:
        stored = conn.execute(
            select(operator.c.password_hash).where(operator.c.login_id == "cs_new")
        ).scalar_one()
    assert temp not in stored
    assert temp not in str(outbox_payloads(engine, "HANDLER"))


def test_new_account_is_synced_to_argus_without_role(admin, engine):
    create(admin)
    [event] = [e for e in outbox_payloads(engine, "HANDLER") if e["type"] == "HANDLER_CREATED"]
    assert event["handler"] == {
        "login_id": "cs_new",
        "name": "신입상담",
        "team": "CS",
        "employment_status": "ACTIVE",
    }


def test_first_login_must_change_the_temporary_password(admin, app):
    temp = create(admin).json()["temporary_password"]
    with TestClient(app, client=TEST_CLIENT_ADDR) as newbie:
        res = login(newbie, "cs_new", temp)
        assert res.status_code == 200 and res.json()["must_change_password"] is True
        blocked = newbie.get("/admin/members")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert newbie.get("/admin/auth/me").json()["must_change_password"] is True

        weak = newbie.post(
            "/admin/auth/password", json={"current_password": temp, "new_password": "short"}
        )
        assert weak.status_code == 400
        changed = newbie.post(
            "/admin/auth/password", json={"current_password": temp, "new_password": NEW_PASSWORD}
        )
        assert changed.status_code == 204
        assert newbie.get("/admin/members").status_code == 200
    with TestClient(app, client=TEST_CLIENT_ADDR) as again:
        assert login(again, "cs_new", temp).status_code == 401  # 임시 비밀번호는 더 못 쓴다
        assert login(again, "cs_new", NEW_PASSWORD).json()["must_change_password"] is False


def test_duplicate_login_id_is_409(admin, ops_id):
    assert create(admin, login_id="ops_park").json()["error"]["code"] == "LOGIN_ID_TAKEN"


# ── 사유 필수 ──────────────────────────────────────────────


@pytest.mark.parametrize("reason", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_every_change_needs_a_reason(admin, ops_id, engine, reason):
    body = {} if reason is None else {"reason": reason}
    new = {"login_id": "x_new", "name": "가상", "team": "CS", "role": "CS"}
    assert admin.post(ACCOUNTS, json=new | body).status_code == 400
    assert admin.post(f"{ACCOUNTS}/{ops_id}/change", json={"role": "CS"} | body).status_code == 400
    assert admin.post(f"{ACCOUNTS}/{ops_id}/terminate", json=body).status_code == 400
    assert history(engine) == []


# ── 변경·말소 ─────────────────────────────────────────────


def test_change_role_records_before_and_after(admin, ops_id, engine):
    res = admin.post(
        f"{ACCOUNTS}/{ops_id}/change", json={"role": "CS", "team": "CS", "reason": "CS팀 이동"}
    )
    assert res.status_code == 200 and (res.json()["role"], res.json()["team"]) == ("CS", "CS")
    [change] = history(engine)
    assert change["change_type"] == "CHANGE"
    assert (change["before_role"], change["after_role"]) == ("OPS", "CS")
    assert (change["before_team"], change["after_team"]) == ("OPS", "CS")
    [event] = outbox_payloads(engine, "HANDLER")
    assert event["type"] == "HANDLER_UPDATED" and "role" not in event["handler"]
    assert event["handler"]["team"] == "CS"


def test_change_without_difference_is_409(admin, ops_id):
    res = admin.post(f"{ACCOUNTS}/{ops_id}/change", json={"role": "OPS", "reason": "확인"})
    assert res.json()["error"]["code"] == "NO_CHANGE"


def test_terminate_blocks_login_and_records_revoke(admin, ops_id, engine, app):
    with TestClient(app, client=TEST_CLIENT_ADDR) as ops:
        assert login(ops).status_code == 200
        res = admin.post(f"{ACCOUNTS}/{ops_id}/terminate", json={"reason": "2026-10-10 퇴사"})
        assert res.status_code == 200 and res.json()["employment_status"] == "TERMINATED"
        # 로그인해 있던 세션도 다음 요청부터 막힌다 — DB 토큰 발급 포함
        assert ops.post("/admin/db-tokens").status_code == 401
    with TestClient(app, client=TEST_CLIENT_ADDR) as again:
        assert login(again).json()["error"]["code"] == "ACCOUNT_DISABLED"

    [revoke] = history(engine)
    assert revoke["change_type"] == "REVOKE"
    assert (revoke["before_status"], revoke["after_status"]) == ("ACTIVE", "TERMINATED")
    [event] = outbox_payloads(engine, "HANDLER")
    assert event["type"] == "HANDLER_TERMINATED" and "terminated_at" in event["handler"]
    # 말소한 계정은 더 바꿀 수 없다
    again = admin.post(f"{ACCOUNTS}/{ops_id}/change", json={"role": "CS", "reason": "x"})
    assert again.json()["error"]["code"] == "ACCOUNT_TERMINATED"


def test_cannot_change_own_account(admin, engine):
    with engine.connect() as conn:
        me = conn.execute(
            select(operator.c.id).where(operator.c.login_id == "admin_han")
        ).scalar_one()
    for path, body in (
        (f"{ACCOUNTS}/{me}/change", {"role": "OPS", "reason": "x"}),
        (f"{ACCOUNTS}/{me}/terminate", {"reason": "x"}),
    ):
        res = admin.post(path, json=body)
        assert res.status_code == 403 and res.json()["error"]["code"] == "SELF_CHANGE_FORBIDDEN"
    assert history(engine) == []


@pytest.mark.parametrize("role", ["OPS", "CS", "MARKETING"])
def test_only_admin_manages_accounts(app, engine, password_hash, ops_id, role):
    add_operator(engine, password_hash, login_id="someone", role=role)
    with TestClient(app, client=TEST_CLIENT_ADDR) as c:
        assert login(c, "someone").status_code == 200
        assert c.get(ACCOUNTS).status_code == 403
        assert create(c).status_code == 403
        assert (
            c.post(f"{ACCOUNTS}/{ops_id}/change", json={"role": "ADMIN", "reason": "x"}).status_code
            == 403
        )
        assert c.post(f"{ACCOUNTS}/{ops_id}/terminate", json={"reason": "x"}).status_code == 403
        assert c.post(f"{ACCOUNTS}/history/search", json={}).status_code == 403
    assert history(engine) == []


# ── 권한 이력 ─────────────────────────────────────────────


def test_history_search_by_target_and_period(admin, ops_id):
    create(admin)
    admin.post(f"{ACCOUNTS}/{ops_id}/change", json={"role": "CS", "reason": "이동"})

    everything = admin.post(f"{ACCOUNTS}/history/search", json={}).json()
    assert everything["total"] == 2
    assert [i["change_type"] for i in everything["items"]] == ["CHANGE", "GRANT"]  # 최신 먼저
    assert everything["items"][0]["actor_login_id"] == "admin_han"

    only_ops = admin.post(f"{ACCOUNTS}/history/search", json={"login_id": "ops_park"}).json()
    assert [i["login_id"] for i in only_ops["items"]] == ["ops_park"]
    past = admin.post(
        f"{ACCOUNTS}/history/search", json={"date_from": "2020-01-01", "date_to": "2020-12-31"}
    ).json()
    assert past["total"] == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE operator_permission_history SET reason = '고침'",
        "DELETE FROM operator_permission_history",
    ],
)
def test_history_is_append_only(admin, engine, statement):
    create(admin)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))
    assert len(history(engine)) == 1


def test_account_screens_are_not_access_logged(admin, ops_id, engine):
    # 회원 개인정보 처리가 아니므로 Agent 기록 대상이 아니다 — 권한 이력이 증적
    create(admin)
    admin.get(ACCOUNTS)
    admin.post(f"{ACCOUNTS}/history/search", json={})
    paths = [e["request"]["path"] for e in outbox_payloads(engine)]
    assert not [p for p in paths if p.startswith(ACCOUNTS)]
