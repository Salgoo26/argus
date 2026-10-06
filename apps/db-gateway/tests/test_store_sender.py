"""원문 저장소·전송 버퍼(store) + 전송 루프(sender) — DB 없이 실행"""

import json
import sqlite3
import uuid

import pytest

from app.sender import HttpResult, Sender, backoff_seconds, sign
from app.store import fingerprint

SECRET = b"test-ingest-secret"


def _raw_and_event() -> tuple[dict, dict]:
    event_id = str(uuid.uuid4())
    raw = {"kind": "LOGIN", "event_id": event_id, "occurred_at": "2026-10-06T10:00:00+00:00"}
    event = {"event_id": event_id, "action": "LOGIN", "context": {"db_user": "platform"}}
    return raw, event


# ── store ────────────────────────────────────────────────


def test_record_adds_raw_reference_and_fingerprint(store):
    raw, event = _raw_and_event()
    store.record(raw, event)
    [row] = store.outbox_rows()
    assert row["status"] == "PENDING"
    assert row["payload"]["context"]["raw_ref"] == raw["event_id"]
    assert row["payload"]["context"]["raw_fingerprint"] == fingerprint(raw)
    assert store.raw(raw["event_id"]) == (raw, fingerprint(raw))


def test_fingerprint_changes_when_raw_changes():
    raw, _ = _raw_and_event()
    assert fingerprint(raw) == fingerprint(dict(reversed(list(raw.items()))))  # 키 순서 무관
    assert fingerprint(raw) != fingerprint({**raw, "kind": "QUERY"})


def test_raw_and_event_are_written_together(store):
    raw, event = _raw_and_event()
    store.record(raw, event)
    # 같은 event_id로 다시 — 버퍼 쪽 UNIQUE 위반이면 원문도 들어가지 않아야 한다
    other_raw = {**raw, "event_id": str(uuid.uuid4())}
    with pytest.raises(sqlite3.IntegrityError):
        store.record(other_raw, event)
    assert store.raw(other_raw["event_id"]) is None


def test_raw_record_is_append_only(store):
    raw, event = _raw_and_event()
    store.record(raw, event)
    conn = store._conn()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE raw_record SET record = '{}'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM raw_record")


# ── sender ───────────────────────────────────────────────


class FakeArgus:
    def __init__(self, *responses: HttpResult) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, bytes]] = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.responses.pop(0)


def _ok(accepted: int, rejected: list[dict] | None = None) -> HttpResult:
    body = {"accepted": accepted, "duplicates": 0, "rejected": rejected or []}
    return HttpResult(200, json.dumps(body).encode())


def _filled(store, n: int = 2) -> list[dict]:
    events = []
    for _ in range(n):
        raw, event = _raw_and_event()
        store.record(raw, event)
        events.append(event)
    return events


def test_sends_signed_batch_as_platform_source_and_deletes_on_accept(store):
    _filled(store)
    argus = FakeArgus(_ok(2))
    assert Sender(store, "http://argus", SECRET, argus, clock=lambda: 1_000).run_once() == 2

    url, headers, body = argus.calls[0]
    assert url == "http://argus/ingest/v1/access-logs"
    assert headers["X-Argus-Source"] == "PLATFORM"  # 3티어와 같은 출처 → 취급자 매칭이 같다
    assert headers["X-Argus-Signature"] == sign(SECRET, "1000", body)
    assert len(json.loads(body)["events"]) == 2
    assert store.outbox_rows() == []


def test_rejected_event_becomes_dead_and_others_are_deleted(store):
    first, second = _filled(store)
    rejected = [{"event_id": first["event_id"], "code": "INVALID_FIELD", "message": "x"}]
    Sender(store, "http://argus", SECRET, FakeArgus(_ok(1, rejected))).run_once()
    [row] = store.outbox_rows()
    assert row["event_id"] == first["event_id"] and row["status"] == "DEAD"


@pytest.mark.parametrize("status", [None, 401, 429, 500, 503])
def test_transient_or_auth_failure_keeps_pending_with_backoff(store, status):
    # 접속기록은 스스로 버리지 않는다 (CLAUDE.md 3절 #7)
    _filled(store, 1)
    Sender(store, "http://argus", SECRET, FakeArgus(HttpResult(status))).run_once()
    [row] = store.outbox_rows()
    assert row["status"] == "PENDING" and row["attempts"] == 1
    assert store.due(10, now=0) == []  # 백오프 동안은 다시 집지 않는다


def test_bad_request_marks_whole_batch_dead(store):
    _filled(store)
    Sender(store, "http://argus", SECRET, FakeArgus(HttpResult(400, b"{}"))).run_once()
    assert {row["status"] for row in store.outbox_rows()} == {"DEAD"}


def test_too_large_batch_is_split(store):
    _filled(store, 4)
    argus = FakeArgus(HttpResult(413), _ok(2), _ok(2))
    Sender(store, "http://argus", SECRET, argus).run_once()
    assert [len(json.loads(call[2])["events"]) for call in argus.calls] == [4, 2, 2]
    assert store.outbox_rows() == []


def test_backoff_doubles_up_to_one_hour():
    assert [backoff_seconds(n) for n in (1, 2, 3, 7, 20)] == [60, 120, 240, 3600, 3600]
