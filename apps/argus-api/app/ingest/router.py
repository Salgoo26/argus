"""시스템 간 수신 API (api-spec 2·3절)

- ① 접속기록 수집 — POST /ingest/v1/access-logs
- ② 취급자 동기화 — POST /ingest/v1/handler-events

둘 다 같은 HMAC 서명 검증(auth.py), 같은 배치 한도, 같은 응답 형식
({accepted, duplicates, rejected})을 쓴다.
"""

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine

from app.errors import ApiError
from app.handlers.sync import apply_handler_events
from app.ingest.auth import VerifiedRequest, verify_signed_request
from app.ingest.handler_validation import HandlerState, validate_handler_event
from app.ingest.validation import Rejection, validate_event
from app.ledger.append import append_access_logs
from app.models import source_system

router = APIRouter(prefix="/ingest/v1")

MAX_EVENTS = 100  # api-spec 1-1


def _parse_events(body: bytes) -> list:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(400, "MALFORMED_JSON", "request body is not valid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ApiError(400, "MISSING_EVENTS", "request body must have an 'events' array")
    events = payload["events"]
    if len(events) > MAX_EVENTS:
        # 400이면 relay가 배치 전체를 DEAD로 보낸다. 413이면 절반으로 쪼개 재전송한다(api-spec 1-5)
        # → 접속기록을 버리지 않는 쪽 (implementation-log 2026-09-29 설계 변경)
        raise ApiError(413, "TOO_MANY_EVENTS", f"at most {MAX_EVENTS} events per request")
    return events


def _source_system_id(conn: Connection, code: str) -> int:
    return conn.execute(select(source_system.c.id).where(source_system.c.code == code)).scalar_one()


def _response(accepted: int, duplicates: int, rejected: list[Rejection]) -> dict:
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": [
            {"event_id": r.event_id, "code": r.code, "message": r.message} for r in rejected
        ],
    }


@router.post("/access-logs")
def ingest_access_logs(
    request: Request, verified: Annotated[VerifiedRequest, Depends(verify_signed_request)]
) -> dict:
    # 서명 검증(async, 이벤트 루프)이 끝난 뒤 이 함수는 스레드풀에서 동기로 실행된다
    events = _parse_events(verified.body)
    engine: Engine = request.app.state.engine
    now = datetime.now(UTC)

    entries: list[dict] = []
    rejected: list[Rejection] = []
    for raw in events:
        outcome = validate_event(raw, now)
        if isinstance(outcome, Rejection):
            rejected.append(outcome)
        else:
            entries.append(outcome)

    with engine.begin() as conn:
        source_id = _source_system_id(conn, verified.source_code)
        for entry in entries:
            entry["source_system_id"] = source_id
        result = append_access_logs(conn, entries, received_at=now) if entries else None

    return _response(
        len(result.inserted_ids) if result else 0,
        len(result.duplicate_event_ids) if result else 0,
        rejected,
    )


@router.post("/handler-events")
def ingest_handler_events(
    request: Request, verified: Annotated[VerifiedRequest, Depends(verify_signed_request)]
) -> dict:
    events = _parse_events(verified.body)
    engine: Engine = request.app.state.engine
    now = datetime.now(UTC)

    states: list[HandlerState] = []
    rejected: list[Rejection] = []
    for raw in events:
        outcome = validate_handler_event(raw, now)
        if isinstance(outcome, Rejection):
            rejected.append(outcome)
        else:
            states.append(outcome)

    accepted = duplicates = 0
    if states:
        with engine.begin() as conn:
            source_id = _source_system_id(conn, verified.source_code)
            accepted, duplicates = apply_handler_events(conn, source_id, states)

    return _response(accepted, duplicates, rejected)
