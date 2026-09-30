"""relay — Argus 응답별 outbox 처리 (api-spec 1-4·1-5), 서명, 백오프, 동시 실행"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import outbox
from app.outbox import enqueue
from app.relay import BATCH_SIZE, HttpResult, Relay, backoff_seconds

SECRET = b"test-relay-secret"  # 테스트 전용 더미 값
NOW = 1_790_000_000  # 고정 시각 — 서명 검증을 결정적으로


class FakeArgus:
    """relay가 보낸 요청을 기록하고, 정해 둔 응답을 돌려주는 가짜 수집 서버"""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> HttpResult:
        events = json.loads(body)["events"]
        self.calls.append({"url": url, "headers": headers, "body": body, "events": events})
        return self.respond(events)


def ok(rejected_ids=()):
    def respond(events):
        rejected = [
            {"event_id": e["event_id"], "code": "INVALID_FIELD", "message": "bad"}
            for e in events
            if e["event_id"] in rejected_ids
        ]
        body = {"accepted": len(events) - len(rejected), "duplicates": 0, "rejected": rejected}
        return HttpResult(200, json.dumps(body).encode())

    return respond


def status(code: int, error_code: str | None = None):
    body = (
        json.dumps({"error": {"code": error_code, "message": "x"}}).encode() if error_code else b""
    )
    return lambda _events: HttpResult(code, body)


def _event(**overrides) -> dict:
    return {"event_id": str(uuid.uuid4()), "action": "READ", **overrides}


def _add(engine, count: int, topic: str = "ACCESS_LOG") -> list[str]:
    ids = []
    with engine.begin() as conn:
        for _ in range(count):
            event = _event()
            enqueue(conn, topic, event)
            ids.append(event["event_id"])
    return ids


def _rows(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(outbox).order_by(outbox.c.id)).mappings()
        return [dict(r) for r in rows]


def _relay(engine, argus) -> Relay:
    return Relay(engine, "http://argus-api:8000", SECRET, poster=argus, clock=lambda: NOW)


# ── 200: 건별 판정 ───────────────────────────────────────


def test_accepted_events_are_deleted(engine):
    event_ids = _add(engine, 3)
    argus = FakeArgus(ok())

    _relay(engine, argus).run_once()

    assert _rows(engine) == []
    [call] = argus.calls
    assert call["url"] == "http://argus-api:8000/ingest/v1/access-logs"
    assert [e["event_id"] for e in call["events"]] == event_ids  # 적재 순서대로


def test_request_is_signed_like_argus_expects(engine):
    _add(engine, 1)
    argus = FakeArgus(ok())
    _relay(engine, argus).run_once()

    headers, body = argus.calls[0]["headers"], argus.calls[0]["body"]
    assert headers["X-Argus-Source"] == "PLATFORM"
    assert headers["X-Argus-Timestamp"] == str(NOW)
    expected = hmac.new(SECRET, f"{NOW}.".encode() + body, hashlib.sha256).hexdigest()
    assert headers["X-Argus-Signature"] == "v1=" + expected


def test_rejected_event_becomes_dead_and_the_rest_are_deleted(engine):
    first, second, third = _add(engine, 3)
    _relay(engine, FakeArgus(ok(rejected_ids={second}))).run_once()

    [row] = _rows(engine)
    assert str(row["event_id"]) == second
    assert row["status"] == "DEAD"  # 조용히 버리지 않는다 — 사람이 확인
    assert row["last_error"].startswith("INVALID_FIELD")


def test_handler_topic_goes_to_handler_endpoint(engine):
    _add(engine, 1, topic="HANDLER")
    argus = FakeArgus(ok())
    _relay(engine, argus).run_once()
    assert argus.calls[0]["url"].endswith("/ingest/v1/handler-events")


def test_batch_is_at_most_100(engine):
    _add(engine, BATCH_SIZE + 20)
    argus = FakeArgus(ok())
    relay = _relay(engine, argus)

    assert relay.run_once() == BATCH_SIZE
    assert len(argus.calls[0]["events"]) == BATCH_SIZE
    relay.run_once()
    assert _rows(engine) == []


# ── 실패 응답 ─────────────────────────────────────────────


def test_400_marks_the_whole_batch_dead(engine):
    _add(engine, 2)
    _relay(engine, FakeArgus(status(400, "MALFORMED_JSON"))).run_once()
    rows = _rows(engine)
    assert [r["status"] for r in rows] == ["DEAD", "DEAD"]
    assert rows[0]["last_error"] == "400 MALFORMED_JSON"


@pytest.mark.parametrize(
    ("respond", "error"),
    [
        (status(401, "INVALID_SIGNATURE"), "INVALID_SIGNATURE"),  # 키 문제 — 버리지 않는다
        (status(503), "HTTP_503"),
        (status(429), "HTTP_429"),
        (status(404), "HTTP_404"),  # 주소 설정 실수도 사람이 고치면 재개
        (lambda _events: HttpResult(None), "NETWORK_ERROR"),
        (lambda _events: HttpResult(200, b"not json"), "UNREADABLE_RESPONSE"),
    ],
    ids=["401", "503", "429", "404", "network", "unreadable-200"],
)
def test_undelivered_events_stay_pending_with_backoff(engine, respond, error):
    _add(engine, 2)
    before = datetime.now(UTC)

    _relay(engine, FakeArgus(respond)).run_once()

    for row in _rows(engine):
        assert row["status"] == "PENDING"  # 접속기록은 스스로 버리지 않는다 (절대 규칙 #7)
        assert row["attempts"] == 1
        assert row["last_error"] == error
        wait = row["next_retry_at"] - before
        assert timedelta(seconds=55) < wait < timedelta(seconds=70)  # 첫 재시도는 1분 뒤


def test_413_splits_the_batch_in_half(engine):
    _add(engine, 4)

    def respond(events):
        return status(413, "TOO_MANY_EVENTS")(events) if len(events) > 2 else ok()(events)

    argus = FakeArgus(respond)
    _relay(engine, argus).run_once()

    assert [len(c["events"]) for c in argus.calls] == [4, 2, 2]
    assert _rows(engine) == []


def test_413_for_a_single_event_is_kept_pending(engine):
    _add(engine, 1)
    _relay(engine, FakeArgus(status(413, "PAYLOAD_TOO_LARGE"))).run_once()
    [row] = _rows(engine)
    assert row["status"] == "PENDING" and row["last_error"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize(
    ("attempts", "seconds"), [(1, 60), (2, 120), (3, 240), (6, 1920), (7, 3600), (30, 3600)]
)
def test_backoff_doubles_up_to_one_hour(attempts, seconds):
    assert backoff_seconds(attempts) == seconds


# ── 보낼 차례·상태 ────────────────────────────────────────


def test_not_yet_due_and_dead_events_are_not_sent(engine):
    waiting, dead = _add(engine, 2)
    with engine.begin() as conn:
        conn.execute(
            update(outbox)
            .where(outbox.c.event_id == uuid.UUID(waiting))
            .values(next_retry_at=datetime.now(UTC) + timedelta(minutes=5))
        )
        conn.execute(
            update(outbox).where(outbox.c.event_id == uuid.UUID(dead)).values(status="DEAD")
        )
    argus = FakeArgus(ok())

    assert _relay(engine, argus).run_once() == 0
    assert argus.calls == []


def test_row_locked_by_another_relay_is_skipped(engine):
    locked, free = _add(engine, 2)
    argus = FakeArgus(ok())
    with engine.connect() as other_relay:
        # 다른 relay가 이 건을 전송 중인 상황 — FOR UPDATE로 잡고 있다
        other_relay.execute(
            select(outbox.c.id).where(outbox.c.event_id == uuid.UUID(locked)).with_for_update()
        )
        _relay(engine, argus).run_once()
        other_relay.rollback()

    assert [e["event_id"] for e in argus.calls[0]["events"]] == [free]
    assert [str(r["event_id"]) for r in _rows(engine)] == [locked]
