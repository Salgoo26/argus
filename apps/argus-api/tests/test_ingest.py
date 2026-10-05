"""① 접속기록 수집 API — 인증, 건별 판정, 멱등, 크기 제한 (api-spec 1·2절)"""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ledger.hashchain import verify_chain
from app.models import access_log

from conftest import make_event, post_events, signed_headers

URL = "/ingest/v1/access-logs"


def _rows(app_engine):
    with app_engine.connect() as conn:
        return conn.execute(select(access_log).order_by(access_log.c.id)).mappings().all()


# ── 정상 수집 ─────────────────────────────────────────────


def test_accepts_batch_and_stores_in_chain(client, app_engine):
    events = [make_event() for _ in range(3)]
    res = post_events(client, events)

    assert res.status_code == 200
    assert res.json() == {"accepted": 3, "duplicates": 0, "rejected": []}
    rows = _rows(app_engine)
    assert [str(r["event_id"]) for r in rows] == [e["event_id"] for e in events]
    first = rows[0]
    assert first["actor_login_id"] == "ops_park"
    assert first["subject_ids"] == ["10293", "10294"]
    assert first["subject_count"] == 2
    assert first["request_query_keys"] == ["team"]
    assert first["context"] is None  # 빈 context {}는 NULL로 저장
    with app_engine.connect() as conn:
        assert verify_chain(conn).ok


def test_login_without_subject_is_accepted(client, app_engine):
    event = make_event(action="LOGIN", data_category="NONE", subject=None, request=None)
    del event["subject"]
    res = post_events(client, [event])
    assert res.json()["accepted"] == 1
    row = _rows(app_engine)[0]
    assert row["subject_type"] is None and row["subject_count"] == 0


def test_past_occurred_at_is_accepted_for_seeding(client):
    # 시드 주입: occurred_at은 과거, X-Argus-Timestamp는 현재 (api-spec 4절)
    past = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    assert post_events(client, [make_event(occurred_at=past)]).json()["accepted"] == 1


def test_undefined_fields_are_ignored_not_stored(client, app_engine):
    # 발신 측 실수로 원본 개인정보 필드가 붙어 와도 저장되지 않는다 (CLAUDE.md 3절 #3)
    event = make_event(member_name="홍길동", email="hong@example.com")
    event["subject"]["phone"] = "010-0000-0000"
    assert post_events(client, [event]).json()["accepted"] == 1
    stored = json.dumps({k: str(v) for k, v in _rows(app_engine)[0].items()}, ensure_ascii=False)
    assert "홍길동" not in stored and "hong@" not in stored and "010-" not in stored


# ── 멱등 ──────────────────────────────────────────────────


def test_resent_event_is_duplicate(client, app_engine):
    event = make_event()
    post_events(client, [event])
    res = post_events(client, [event, make_event()])
    assert res.json() == {"accepted": 1, "duplicates": 1, "rejected": []}
    assert len(_rows(app_engine)) == 2


def test_duplicate_within_same_batch(client):
    event = make_event()
    res = post_events(client, [event, event])
    assert res.json() == {"accepted": 1, "duplicates": 1, "rejected": []}


# ── 건별 판정 (부분 수락) ─────────────────────────────────


def test_partial_acceptance(client, app_engine):
    bad = make_event(action="VIEW")
    res = post_events(client, [make_event(), bad, make_event()])
    body = res.json()
    assert res.status_code == 200
    assert body["accepted"] == 2
    assert body["rejected"] == [
        {"event_id": bad["event_id"], "code": "INVALID_ACTION", "message": "unknown action: VIEW"}
    ]
    assert len(_rows(app_engine)) == 2


def _without(key: str) -> dict:
    event = make_event()
    del event[key]
    return event


FUTURE = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (_without("occurred_at"), "MISSING_FIELD"),
        (_without("client_ip"), "MISSING_FIELD"),
        (make_event(actor={}), "MISSING_FIELD"),
        (make_event(event_id="not-a-uuid"), "INVALID_FIELD"),
        (make_event(action="VIEW"), "INVALID_ACTION"),
        (make_event(data_category="SECRET"), "INVALID_CATEGORY"),
        (make_event(access_path="WEB"), "INVALID_ACCESS_PATH"),
        (make_event(occurred_at="2026-09-15 23:41"), "INVALID_TIMESTAMP"),  # 오프셋 없음
        (make_event(occurred_at=FUTURE), "INVALID_TIMESTAMP"),  # 미래 5분 초과
        (make_event(client_ip="999.1.1.1"), "INVALID_IP"),
        (make_event(subject=None), "SUBJECT_REQUIRED"),
        (make_event(context={"note": "x"}), "UNKNOWN_CONTEXT_KEY"),
        (make_event(result="OK"), "INVALID_FIELD"),
        # Argus 자체 기록 전용 코드값은 외부 출처에서 받지 않는다
        (make_event(action="UNMASK", context={"reason": "x"}), "INVALID_ACTION"),
        (make_event(data_category="ACCESS_LOG"), "INVALID_CATEGORY"),
        # 설계 변경: 코드표에 없던 필드 오류 → INVALID_FIELD
        (make_event(actor={"login_id": "x" * 65}), "INVALID_FIELD"),
        (
            make_event(subject={"type": "MEMBER", "ids": ["1"] * 1001, "count": 1001}),
            "INVALID_FIELD",
        ),
        (make_event(subject={"type": "MEMBER", "ids": ["1", "2"], "count": 1}), "INVALID_FIELD"),
        (make_event(request={"path": "/admin/members?name=hong"}), "INVALID_FIELD"),
    ],
)
def test_rejection_codes(client, event, code):
    res = post_events(client, [event])
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] == 0
    assert body["rejected"][0]["code"] == code


def test_personal_data_in_subject_ids_is_rejected_without_echo(client):
    # 내부 PK 자리에 이메일이 들어오면 거부하고, 응답에 그 값을 되풀이하지 않는다
    event = make_event(subject={"type": "MEMBER", "ids": ["hong@example.com"], "count": 1})
    res = post_events(client, [event])
    assert res.json()["rejected"][0]["code"] == "INVALID_FIELD"
    assert "hong@" not in res.text


# ── DB 경로 (2티어 게이트웨이, api-spec 2-2·2-3 v0.5) ─────────

FINGERPRINT = "sha256:" + "ab" * 32


def _db_context(**overrides) -> dict:
    context = {
        "db_user": "platform",
        "sql_normalized": "SELECT id, name FROM member WHERE email = $1",
        "tables": ["member"],
        "columns": ["member.id", "member.name", "member.email"],
        "row_count": 1,
        "raw_ref": "gw:2026-10-06:000001",
        "raw_fingerprint": FINGERPRINT,
        "subject_unresolved": False,
        "token_id": str(uuid.uuid4()),
    }
    context.update(overrides)
    return context


def _db_event(context: dict | None = None, **overrides) -> dict:
    fields = {
        "access_path": "DB",
        "action": "READ",
        "subject": {"type": "MEMBER", "ids": ["10293"], "count": 1},
        "request": None,
        "context": _db_context() if context is None else context,
    }
    return make_event(**(fields | overrides))


def test_db_event_is_accepted_with_normalized_sql(client, app_engine):
    event = _db_event()
    assert post_events(client, [event]).json()["accepted"] == 1
    row = _rows(app_engine)[0]
    assert row["access_path"] == "DB"
    assert row["context"] == event["context"]


def test_db_login_needs_no_sql(client):
    # 연결 인증은 문장이 없다 — 정규화 SQL·건수 없이 원문 참조·지문만
    context = {"db_user": "platform", "raw_ref": "gw:login:1", "raw_fingerprint": FINGERPRINT}
    event = _db_event(context, action="LOGIN", data_category="NONE")
    del event["subject"]
    assert post_events(client, [event]).json()["accepted"] == 1


def _db_without(key: str) -> dict:
    context = _db_context()
    del context[key]
    return _db_event(context)


@pytest.mark.parametrize(
    ("event", "code"),
    [
        # 원문 SQL 키는 v0.5에서 폐기 — 어느 경로로도 받지 않는다
        (make_event(context={"query": "SELECT 1"}), "UNKNOWN_CONTEXT_KEY"),
        (_db_event(_db_context(query="SELECT 1")), "UNKNOWN_CONTEXT_KEY"),
        # DB 키는 DB 경로 전용
        (make_event(context={"row_count": 1}), "UNKNOWN_CONTEXT_KEY"),
        (make_event(context={"sql_normalized": "SELECT 1"}), "UNKNOWN_CONTEXT_KEY"),
        (_db_without("db_user"), "MISSING_FIELD"),
        (_db_without("raw_ref"), "MISSING_FIELD"),
        (_db_without("raw_fingerprint"), "MISSING_FIELD"),
        (_db_without("sql_normalized"), "MISSING_FIELD"),
        (_db_without("row_count"), "MISSING_FIELD"),
        (_db_event(_db_context(db_user="app user")), "INVALID_FIELD"),
        (_db_event(_db_context(sql_normalized="SELECT " + "x" * 4000)), "INVALID_FIELD"),
        (_db_event(_db_context(tables=["member"] * 51)), "INVALID_FIELD"),
        (_db_event(_db_context(columns=["member.email; --"])), "INVALID_FIELD"),
        (_db_event(_db_context(row_count=-1)), "INVALID_FIELD"),
        (_db_event(_db_context(raw_ref="../etc/passwd")), "INVALID_FIELD"),
        (_db_event(_db_context(raw_fingerprint="sha256:" + "AB" * 32)), "INVALID_FIELD"),
        (_db_event(_db_context(subject_unresolved="yes")), "INVALID_FIELD"),
        (_db_event(_db_context(token_id="not-a-uuid")), "INVALID_FIELD"),
    ],
)
def test_db_context_rejection_codes(client, event, code):
    body = post_events(client, [event]).json()
    assert body["accepted"] == 0
    assert body["rejected"][0]["code"] == code


@pytest.mark.parametrize(
    "raw_sql",
    [
        "SELECT id FROM member WHERE email = 'hong@example.com'",
        "SELECT id FROM member WHERE email = $$hong@example.com$$",
        "SELECT id FROM member WHERE email = $q$hong@example.com$q$",
    ],
)
def test_raw_sql_literal_is_rejected_without_echo(client, app_engine, raw_sql):
    # 정규화가 깨져 원문이 실려 와도 저장하지 않고, 응답에 값을 되풀이하지 않는다 (절대 규칙 #3)
    res = post_events(client, [_db_event(_db_context(sql_normalized=raw_sql))])
    assert res.json()["rejected"][0]["code"] == "INVALID_FIELD"
    assert "hong@" not in res.text
    assert _rows(app_engine) == []


# ── 인증 (api-spec 1-2) ───────────────────────────────────


def _body() -> bytes:
    return json.dumps({"events": [make_event()]}).encode()


def test_signature_mismatch_is_401(client, app_engine):
    body = _body()
    headers = signed_headers(body, secret="wrong-secret")
    res = client.post(URL, content=body, headers=headers)
    assert res.status_code == 401
    assert res.json() == {"error": {"code": "INVALID_SIGNATURE", "message": "signature mismatch"}}
    assert _rows(app_engine) == []


def test_body_tampered_after_signing_is_401(client):
    body = _body()
    headers = signed_headers(body)
    tampered = body.replace(b"ops_park", b"ops_kim_")
    assert client.post(URL, content=tampered, headers=headers).status_code == 401


def test_unknown_source_is_401(client):
    body = _body()
    res = client.post(URL, content=body, headers=signed_headers(body, source="ARGUS"))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNKNOWN_SOURCE"


@pytest.mark.parametrize("offset", [-301, 301])
def test_timestamp_outside_tolerance_is_401(client, offset):
    body = _body()
    res = client.post(
        URL, content=body, headers=signed_headers(body, timestamp=int(time.time()) + offset)
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_TIMESTAMP"


def test_missing_auth_headers_is_401(client):
    res = client.post(URL, content=_body(), headers={"Content-Type": "application/json"})
    assert res.status_code == 401


# ── 요청 전체 오류 ────────────────────────────────────────


def test_malformed_json_is_400(client):
    body = b'{"events": ['
    res = client.post(URL, content=body, headers=signed_headers(body))
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "MALFORMED_JSON"


def test_missing_events_is_400(client):
    body = b'{"items": []}'
    res = client.post(URL, content=body, headers=signed_headers(body))
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "MISSING_EVENTS"


def test_more_than_100_events_is_413(client, app_engine):
    # 설계 변경: 400(→ relay가 DEAD 처리) 대신 413(→ relay가 절반으로 쪼개 재전송)
    res = post_events(client, [make_event() for _ in range(101)])
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "TOO_MANY_EVENTS"
    assert _rows(app_engine) == []


def test_body_over_1mb_is_413(client):
    body = json.dumps({"events": [], "pad": "x" * (1024 * 1024)}).encode()
    res = client.post(URL, content=body, headers=signed_headers(body))
    assert res.status_code == 413


def test_empty_batch_is_ok(client):
    assert post_events(client, []).json() == {"accepted": 0, "duplicates": 0, "rejected": []}


def test_event_ids_in_response_are_strings(client):
    # 응답의 event_id는 발신 측 outbox 매칭 키 — 받은 그대로 돌려준다
    eid = str(uuid.uuid4()).upper()
    res = post_events(client, [make_event(event_id=eid, action="VIEW")])
    assert res.json()["rejected"][0]["event_id"] == eid
