"""② 취급자 동기화 API — 멱등·순서 역전·A5 계정 부수 효과·건별 판정 (api-spec 3절)"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import argus_user, handler

from conftest import signed_headers

URL = "/ingest/v1/handler-events"
BASE = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)


def at(seconds: float) -> str:
    """BASE 기준 상대 시각 — 마이크로초 정밀도로 보낸다 (api-spec 3-1 #4)"""
    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def make_handler_event(
    t: float = 0, *, type_: str = "HANDLER_CREATED", event_id: str | None = None, **handler_fields
) -> dict:
    fields = {
        "login_id": "ops_park",
        "name": "박운영",  # 가상 인물
        "team": "OPS",
        "employment_status": "ACTIVE",
    }
    fields.update(handler_fields)
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "type": type_,
        "occurred_at": at(t),
        "handler": fields,
    }


def terminated_event(t: float, **fields) -> dict:
    # 퇴직 예정 시각(terminated_at)은 occurred_at보다 미래여도 된다
    defaults = {"employment_status": "TERMINATED", "terminated_at": at(t + 3600)}
    return make_handler_event(t, type_="HANDLER_TERMINATED", **(defaults | fields))


def post(client, events: list):
    body = json.dumps({"events": events}).encode()
    return client.post(URL, content=body, headers=signed_headers(body))


def handlers(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(handler).order_by(handler.c.id)).mappings()]


def accounts(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        rows = conn.execute(select(argus_user).order_by(argus_user.c.id)).mappings()
        return [dict(r) for r in rows]


@pytest.fixture(autouse=True)
def clean_handlers(admin_engine):
    # TRUNCATE ... CASCADE는 argus_user를 참조하는 탐지 룰 등까지 비우므로 DELETE로 정리
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))
        conn.execute(text("DELETE FROM handler"))
    yield


# ── 생성·A5 계정 ──────────────────────────────────────────


def test_created_handler_is_stored_with_a5_account(client, app_engine):
    res = post(client, [make_handler_event(0)])

    assert res.status_code == 200
    assert res.json() == {"accepted": 1, "duplicates": 0, "rejected": []}
    [row] = handlers(app_engine)
    assert row["login_id"] == "ops_park" and row["team"] == "OPS"
    assert row["employment_status"] == "ACTIVE" and row["terminated_at"] is None
    assert row["last_event_at"] == BASE
    [account] = accounts(app_engine)
    assert account["login_id"] == "ops_park"
    assert account["role"] == "HANDLER" and account["handler_id"] == row["id"]
    assert account["status"] == "ACTIVE"


def test_a5_initial_password_is_an_unknown_random_argon2id_hash(client, app_engine):
    post(client, [make_handler_event(0), make_handler_event(0, login_id="cs_kim")])
    hashes = [a["password_hash"] for a in accounts(app_engine)]
    assert all(h.startswith("$argon2id$") for h in hashes)
    assert hashes[0] != hashes[1]  # 계정마다 다른 무작위 값


def test_only_minimal_handler_fields_are_stored(client, app_engine):
    # 연락처·사번 등은 받지도 저장하지도 않는다 (api-spec 3-1 최소수집)
    event = make_handler_event(0, phone="010-0000-1234", employee_no="E-1001")
    assert post(client, [event]).json()["accepted"] == 1
    stored = json.dumps([{k: str(v) for k, v in r.items()} for r in handlers(app_engine)])
    assert "010-0000" not in stored and "E-1001" not in stored


# ── 멱등·순서 역전 (last_event_at 기준) ───────────────────


def test_resent_event_is_duplicate_and_changes_nothing(client, app_engine):
    event = make_handler_event(0)
    post(client, [event])
    before = handlers(app_engine)

    res = post(client, [event])

    assert res.json() == {"accepted": 0, "duplicates": 1, "rejected": []}
    assert handlers(app_engine) == before  # updated_at까지 그대로
    assert len(accounts(app_engine)) == 1


def test_newer_event_overwrites_state(client, app_engine):
    post(client, [make_handler_event(0)])
    res = post(client, [make_handler_event(10, type_="HANDLER_UPDATED", team="MARKETING")])

    assert res.json()["accepted"] == 1
    [row] = handlers(app_engine)
    assert row["team"] == "MARKETING"
    assert row["last_event_at"] == BASE + timedelta(seconds=10)


def test_old_event_arriving_late_is_counted_as_duplicate(client, app_engine):
    post(client, [make_handler_event(10, type_="HANDLER_UPDATED", team="MARKETING")])
    res = post(client, [make_handler_event(0, team="OPS")])  # 더 옛날 변경이 늦게 도착

    assert res.json() == {"accepted": 0, "duplicates": 1, "rejected": []}
    assert handlers(app_engine)[0]["team"] == "MARKETING"  # 최신 상태 유지


def test_reversed_order_within_one_batch_keeps_latest_state(client, app_engine):
    newer = make_handler_event(10, type_="HANDLER_UPDATED", team="CS")
    older = make_handler_event(0, team="OPS")

    res = post(client, [newer, older])

    assert res.json() == {"accepted": 1, "duplicates": 1, "rejected": []}
    assert handlers(app_engine)[0]["team"] == "CS"


def test_changes_one_microsecond_apart_are_both_applied(client, app_engine):
    # 발신 측이 마이크로초 정밀도로 보내는 이유 — 초 단위면 두 번째가 duplicates로 유실
    first = make_handler_event(0)
    second = make_handler_event(0.000001, type_="HANDLER_UPDATED", team="CS")
    assert post(client, [first, second]).json()["accepted"] == 2
    assert handlers(app_engine)[0]["team"] == "CS"


# ── 퇴직·재입사 ───────────────────────────────────────────


def test_termination_disables_a5_account(client, app_engine):
    post(client, [make_handler_event(0)])
    res = post(client, [terminated_event(10)])

    assert res.json()["accepted"] == 1
    [row] = handlers(app_engine)
    assert row["employment_status"] == "TERMINATED"
    assert row["terminated_at"] == BASE + timedelta(seconds=10 + 3600)
    assert accounts(app_engine)[0]["status"] == "DISABLED"


def test_termination_applied_twice_has_same_result(client, app_engine):
    post(client, [make_handler_event(0)])
    event = terminated_event(10)
    post(client, [event])

    assert post(client, [event]).json()["duplicates"] == 1
    # 퇴직 뒤의 새 변경(이름 정정)도 계정 상태를 바꾸지 않는다
    later = terminated_event(20, name="박운영2")
    later["type"] = "HANDLER_UPDATED"
    assert post(client, [later]).json()["accepted"] == 1
    [account] = accounts(app_engine)
    assert account["status"] == "DISABLED"


def test_rehire_does_not_reenable_a5_account(client, app_engine):
    post(client, [make_handler_event(0), terminated_event(10)])
    res = post(client, [make_handler_event(20, type_="HANDLER_UPDATED")])  # 재입사

    assert res.json()["accepted"] == 1
    assert handlers(app_engine)[0]["employment_status"] == "ACTIVE"
    assert handlers(app_engine)[0]["terminated_at"] is None
    [account] = accounts(app_engine)
    assert account["status"] == "DISABLED"  # 권한 복구는 사람이 판단


def test_handler_first_seen_as_terminated_gets_no_a5_account(client, app_engine):
    assert post(client, [terminated_event(0)]).json()["accepted"] == 1
    assert handlers(app_engine)[0]["employment_status"] == "TERMINATED"
    assert accounts(app_engine) == []


def test_login_id_used_by_officer_is_not_taken_over(client, app_engine, admin_engine):
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO argus_user (login_id, password_hash, role) "
                "VALUES ('ops_park', 'officer-hash', 'OFFICER')"
            )
        )

    assert post(client, [make_handler_event(0)]).json()["accepted"] == 1

    assert len(handlers(app_engine)) == 1  # 취급자 정보는 반영
    [account] = accounts(app_engine)
    assert account["role"] == "OFFICER" and account["handler_id"] is None
    assert account["password_hash"] == "officer-hash"


# ── 건별 판정 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (terminated_event(0, terminated_at=None), "TERMINATED_AT_REQUIRED"),
        (make_handler_event(0, terminated_at=at(0)), "INVALID_FIELD"),  # 재직인데 퇴직 시각
        (make_handler_event(0, type_="HANDLER_TERMINATED"), "INVALID_FIELD"),  # 퇴직 이벤트, 재직
        (terminated_event(0, terminated_at="2026-09-20T18:00:00"), "INVALID_FIELD"),  # 오프셋 없음
        (make_handler_event(0, type_="HANDLER_MOVED"), "INVALID_FIELD"),
        (make_handler_event(0, team="SALES"), "INVALID_FIELD"),
        (make_handler_event(0, employment_status="RETIRED"), "INVALID_FIELD"),
        (make_handler_event(0, login_id="ops park"), "INVALID_FIELD"),
        (make_handler_event(0, name="가" * 51), "INVALID_FIELD"),
        (make_handler_event(0, name="  "), "INVALID_FIELD"),
        (make_handler_event(0, name="박\n운영"), "INVALID_FIELD"),
        (make_handler_event(0, login_id=None), "MISSING_FIELD"),
        (make_handler_event(0, name=""), "MISSING_FIELD"),
        (make_handler_event(0, event_id="not-a-uuid"), "INVALID_FIELD"),
    ],
)
def test_invalid_event_is_rejected(client, app_engine, event, code):
    res = post(client, [event])

    body = res.json()
    assert res.status_code == 200
    assert body["accepted"] == 0
    assert [r["code"] for r in body["rejected"]] == [code]
    assert handlers(app_engine) == []


@pytest.mark.parametrize(
    "occurred_at",
    [
        "2026-09-20T10:00:00",  # 오프셋 없음
        (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),  # 미래 5분 초과
    ],
)
def test_invalid_occurred_at_is_rejected(client, occurred_at):
    event = make_handler_event(0)
    event["occurred_at"] = occurred_at
    [rejection] = post(client, [event]).json()["rejected"]
    assert rejection["code"] == "INVALID_TIMESTAMP"


def test_missing_handler_object_is_rejected(client):
    event = make_handler_event(0)
    del event["handler"]
    [rejection] = post(client, [event]).json()["rejected"]
    assert rejection["code"] == "MISSING_FIELD"


def test_partial_acceptance(client, app_engine):
    bad = terminated_event(0, login_id="cs_kim", terminated_at=None)
    res = post(client, [make_handler_event(0), bad])

    body = res.json()
    assert body["accepted"] == 1
    assert body["rejected"] == [
        {
            "event_id": bad["event_id"],
            "code": "TERMINATED_AT_REQUIRED",
            "message": "handler.terminated_at is required",
        }
    ]
    assert [r["login_id"] for r in handlers(app_engine)] == ["ops_park"]


def test_rejection_message_does_not_echo_employee_name(client):
    [rejection] = post(client, [make_handler_event(0, name="홍길동\t")]).json()["rejected"]
    assert "홍길동" not in json.dumps(rejection, ensure_ascii=False)


# ── 요청 단위 (①과 같은 규약) ─────────────────────────────


def test_bad_signature_is_401(client, app_engine):
    body = json.dumps({"events": [make_handler_event(0)]}).encode()
    res = client.post(URL, content=body, headers=signed_headers(body, secret="wrong-secret"))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert handlers(app_engine) == []


def test_too_many_events_is_413(client):
    res = post(client, [make_handler_event(i) for i in range(101)])
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "TOO_MANY_EVENTS"
