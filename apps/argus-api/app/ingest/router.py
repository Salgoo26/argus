"""① 접속기록 수집 API — POST /ingest/v1/access-logs (api-spec 2절)"""

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.errors import ApiError
from app.ingest.auth import VerifiedRequest, verify_signed_request
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
        source_id = conn.execute(
            select(source_system.c.id).where(source_system.c.code == verified.source_code)
        ).scalar_one()
        for entry in entries:
            entry["source_system_id"] = source_id
        result = append_access_logs(conn, entries, received_at=now) if entries else None

    return {
        "accepted": len(result.inserted_ids) if result else 0,
        "duplicates": len(result.duplicate_event_ids) if result else 0,
        "rejected": [
            {"event_id": r.event_id, "code": r.code, "message": r.message} for r in rejected
        ],
    }
