"""outbox 적재 (architecture 3-3, api-spec 4절)

outbox는 Argus로 보낼 이벤트의 발송 대기함이다. 여기에 넣기만 하면 platform-relay가
나중에 서명해서 보내고, Argus가 받았다고 확인한 뒤에 지운다.

topic별 트랜잭션 규칙:
- HANDLER(취급자 변경): 업무와 같은 트랜잭션 — 직원 변경이 롤백되면 통지도 함께 취소
- ACCESS_LOG(접속기록): 업무와 별도 트랜잭션 — 업무가 실패해도 시도 기록은 남긴다 (PR ③)
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, insert

from app.models import outbox


def enqueue(conn: Connection, topic: str, payload: dict[str, Any]) -> None:
    conn.execute(
        insert(outbox).values(event_id=uuid.UUID(payload["event_id"]), topic=topic, payload=payload)
    )


def handler_event(event_type: str, operator_row: Any, occurred_at: datetime) -> dict[str, Any]:
    """② 취급자 동기화 이벤트 (api-spec 3-1)

    취급자 정보도 개인정보다 — 계정·이름·소속·재직상태·퇴직시각만 보낸다(최소수집).
    비밀번호 해시·권한(role)·실패 횟수는 보내지 않는다.
    occurred_at은 마이크로초까지 — 초 단위면 같은 초의 두 변경 중 뒤의 것이 Argus에서
    duplicates로 판정돼 유실된다 (api-spec 3-1 #4)
    """
    handler: dict[str, Any] = {
        "login_id": operator_row.login_id,
        "name": operator_row.name,
        "team": operator_row.team,
        "employment_status": operator_row.employment_status,
    }
    if operator_row.terminated_at is not None:
        handler["terminated_at"] = operator_row.terminated_at.isoformat(timespec="microseconds")
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "occurred_at": occurred_at.isoformat(timespec="microseconds"),
        "handler": handler,
    }
