"""취급자 동기화 이벤트 건별 검증 (api-spec 3-1, 3-2)

①(validation.py)과 같은 원칙: 이벤트마다 판정하고, 정의되지 않은 필드는 무시하고,
거부 메시지에 입력값을 되풀이하지 않는다(코드값 제외). 취급자(직원)의 이름·소속도 개인정보다.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ingest.validation import (
    _LOGIN_ID,
    _UUID,
    Rejection,
    _parse_timestamp,
    _Reject,
    _require,
)

EVENT_TYPES = frozenset({"HANDLER_CREATED", "HANDLER_UPDATED", "HANDLER_TERMINATED"})
TEAMS = frozenset({"CS", "MARKETING", "OPS"})
EMPLOYMENT_STATUSES = frozenset({"ACTIVE", "TERMINATED"})

_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,50}$")  # 제어문자 없는 1~50자 (handler.name varchar(50))


@dataclass(frozen=True)
class HandlerState:
    """이벤트가 싣고 온 취급자 상태 스냅샷 — handler 테이블 한 행에 대응"""

    login_id: str
    name: str
    team: str | None
    employment_status: str
    terminated_at: datetime | None
    occurred_at: datetime


def _shown(value: Any) -> str:
    # 코드값은 개인정보가 아니므로 메시지에 보여준다 (api-spec 2-5와 동일)
    return value if isinstance(value, str) and len(value) <= 32 else "<invalid>"


def _parse_terminated_at(value: Any) -> datetime:
    # 퇴직 예정 시각이 올 수 있으므로(api-spec 3-1 예시: occurred_at 10시, terminated_at 18시)
    # occurred_at과 달리 미래 시각을 거부하지 않는다
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise _Reject("INVALID_FIELD", "handler.terminated_at must be ISO 8601 with a UTC offset")
    return parsed


def validate_handler_event(raw: Any, now: datetime) -> HandlerState | Rejection:
    if not isinstance(raw, dict):
        return Rejection(None, "INVALID_FIELD", "event must be an object")

    raw_event_id = raw.get("event_id")
    shown_id = raw_event_id if isinstance(raw_event_id, str) and len(raw_event_id) <= 64 else None
    try:
        # event_id는 저장하지 않지만(판정은 last_event_at 기준, api-spec 3-1) 형식은 계약대로 검사
        event_id = _require(raw, "event_id")
        if not isinstance(event_id, str) or not _UUID.fullmatch(event_id):
            raise _Reject("INVALID_FIELD", "event_id must be a UUID")

        event_type = _require(raw, "type")
        _require(raw, "occurred_at")
        handler = raw.get("handler")
        if not isinstance(handler, dict):
            raise _Reject("MISSING_FIELD", "handler is required")
        for key in ("login_id", "name", "employment_status"):
            if handler.get(key) in (None, ""):
                raise _Reject("MISSING_FIELD", f"handler.{key} is required")

        if event_type not in EVENT_TYPES:
            raise _Reject("INVALID_FIELD", f"unknown type: {_shown(event_type)}")

        # 미래 시각을 받아들이면 last_event_at이 미래로 밀려, 그 시각까지의 정상 변경이
        # 전부 duplicates로 무시된다 — ①과 같은 "미래 5분 초과 거부"를 적용
        occurred_at = _parse_timestamp(raw["occurred_at"], now)

        login_id = handler["login_id"]
        if not isinstance(login_id, str) or not _LOGIN_ID.fullmatch(login_id):
            raise _Reject("INVALID_FIELD", "handler.login_id must be 1-64 printable characters")

        name = handler["name"]
        if not isinstance(name, str) or not _NAME.fullmatch(name) or not name.strip():
            raise _Reject("INVALID_FIELD", "handler.name must be 1-50 characters")

        team = handler.get("team")
        if team is not None and team not in TEAMS:
            raise _Reject("INVALID_FIELD", f"unknown handler.team: {_shown(team)}")

        status = handler["employment_status"]
        if status not in EMPLOYMENT_STATUSES:
            raise _Reject("INVALID_FIELD", f"unknown handler.employment_status: {_shown(status)}")

        raw_terminated_at = handler.get("terminated_at")
        if status == "TERMINATED":
            # 퇴직자 룰을 행위 시점 기준으로 판정하려면 퇴직 시각이 필요 (api-spec 3-1, v0.2)
            if raw_terminated_at in (None, ""):
                raise _Reject("TERMINATED_AT_REQUIRED", "handler.terminated_at is required")
            terminated_at = _parse_terminated_at(raw_terminated_at)
        elif raw_terminated_at is not None:
            raise _Reject("INVALID_FIELD", "handler.terminated_at must be empty unless TERMINATED")
        else:
            terminated_at = None

        # 최종 상태는 handler 필드가 정한다(api-spec 3-1). 다만 "퇴직 이벤트인데 재직 상태"는
        # 발신 측 버그이므로 조용히 적용하지 않고 거부한다
        if event_type == "HANDLER_TERMINATED" and status != "TERMINATED":
            raise _Reject("INVALID_FIELD", "HANDLER_TERMINATED requires TERMINATED status")

        return HandlerState(login_id, name, team, status, terminated_at, occurred_at)
    except _Reject as reject:
        return Rejection(shown_id, reject.code, reject.message)
