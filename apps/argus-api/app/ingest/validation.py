"""수집 이벤트 건별 검증 (api-spec 2-2, 2-3, 2-5)

형식 오류 1건 때문에 배치 전체를 거부하지 않도록 이벤트마다 판정하고(api-spec 1-4),
통과한 이벤트는 access_log 컬럼 dict로 바꿔 돌려준다.

원본 개인정보 차단 (CLAUDE.md 3절 #3):
- 정의되지 않은 필드는 무시한다(저장하지 않음, api-spec 6절 하위 호환)
- subject.ids는 내부 PK 형태만 허용 — 이메일·전화번호 같은 값이 섞여 들어오면 거부
- request.path에 쿼리스트링이 붙어 오면 거부 — 검색 조건 "값"이 개인정보일 수 있음
- 거부 메시지에는 입력값을 되풀이하지 않는다(코드값 제외) — 응답·로그로 개인정보가 새지 않게
"""

import ipaddress
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# 코드값 (api-spec 2-3). EXPORT·UNMASK·ACCESS_LOG는 Argus 자체 기록 전용
EXTERNAL_ACTIONS = frozenset({"LOGIN", "READ", "CREATE", "UPDATE", "DELETE", "DOWNLOAD"})
ARGUS_ONLY_ACTIONS = frozenset({"EXPORT", "UNMASK"})
EXTERNAL_CATEGORIES = frozenset({"MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY", "NONE"})
ARGUS_ONLY_CATEGORIES = frozenset({"ACCESS_LOG"})
ACCESS_PATHS = frozenset({"APP", "DB"})
RESULTS = frozenset({"SUCCESS", "FAILURE"})
SUBJECT_TYPES = frozenset({"MEMBER"})
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
CONTEXT_KEYS = frozenset({"ticket_id", "reason", "target", "report_id", "query", "row_count"})

SUBJECT_IDS_LIMIT = 1000  # api-spec 2-2
FUTURE_TOLERANCE = timedelta(minutes=5)  # api-spec 2-5

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SUBJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # 내부 PK — '@', '.', 공백 등 불가
_LOGIN_ID = re.compile(r"^[\x21-\x7e]{1,64}$")  # 공백·제어문자 없는 출력 가능 ASCII
_QUERY_KEY = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,64}$")


@dataclass(frozen=True)
class Rejection:
    event_id: str | None
    code: str
    message: str


class _Reject(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require(event: dict, key: str) -> Any:
    value = event.get(key)
    if value is None or value == "":
        raise _Reject("MISSING_FIELD", f"{key} is required")
    return value


def _code(value: Any, allowed: frozenset, argus_only: frozenset, internal: bool, err: str, name):
    if isinstance(value, str) and (value in allowed or (internal and value in argus_only)):
        return value
    # 코드값은 개인정보가 아니므로 메시지에 그대로 보여준다 (api-spec 2-5 예시와 동일)
    shown = value if isinstance(value, str) and len(value) <= 32 else "<invalid>"
    raise _Reject(err, f"unknown {name}: {shown}")


def _parse_timestamp(value: Any, now: datetime) -> datetime:
    if not isinstance(value, str):
        raise _Reject("INVALID_TIMESTAMP", "occurred_at must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _Reject("INVALID_TIMESTAMP", "occurred_at is not ISO 8601") from None
    if parsed.tzinfo is None:
        raise _Reject("INVALID_TIMESTAMP", "occurred_at must include a UTC offset")
    if parsed > now + FUTURE_TOLERANCE:
        raise _Reject("INVALID_TIMESTAMP", "occurred_at is more than 5 minutes in the future")
    return parsed


def _parse_ip(value: Any) -> str:
    try:
        ip = ipaddress.ip_address(value) if isinstance(value, str) else None
    except ValueError:
        ip = None
    # scope id(fe80::1%eth0)는 inet에 저장할 수 없다
    if ip is None or (isinstance(ip, ipaddress.IPv6Address) and ip.scope_id):
        raise _Reject("INVALID_IP", "client_ip is not a valid IPv4/IPv6 address")
    return ip.compressed


def _parse_subject(raw: Any, action: str) -> dict[str, Any]:
    if raw is None:
        if action != "LOGIN":
            raise _Reject("SUBJECT_REQUIRED", "subject is required unless action is LOGIN")
        return {
            "subject_type": None,
            "subject_ids": None,
            "subject_count": 0,
            "subject_truncated": False,
        }
    if not isinstance(raw, dict):
        raise _Reject("INVALID_FIELD", "subject must be an object")

    subject_type = raw.get("type")
    if subject_type is None:
        raise _Reject("MISSING_FIELD", "subject.type is required")
    if subject_type not in SUBJECT_TYPES:
        raise _Reject("INVALID_FIELD", "subject.type must be MEMBER")

    ids = raw.get("ids")
    if ids is not None:
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise _Reject("INVALID_FIELD", "subject.ids must be an array of strings")
        if len(ids) > SUBJECT_IDS_LIMIT:
            raise _Reject("INVALID_FIELD", f"subject.ids exceeds {SUBJECT_IDS_LIMIT}")
        if not all(_SUBJECT_ID.fullmatch(i) for i in ids):
            raise _Reject("INVALID_FIELD", "subject.ids must contain internal identifiers only")

    count = raw.get("count")
    if count is None:
        if ids is None:
            raise _Reject("MISSING_FIELD", "subject.ids or subject.count is required")
        count = len(ids)
    if not _is_int(count) or count < 0:
        raise _Reject("INVALID_FIELD", "subject.count must be a non-negative integer")
    if ids is not None and count < len(ids):
        raise _Reject("INVALID_FIELD", "subject.count is smaller than the number of subject.ids")

    truncated = raw.get("truncated", False)
    if not isinstance(truncated, bool):
        raise _Reject("INVALID_FIELD", "subject.truncated must be a boolean")

    return {
        "subject_type": subject_type,
        "subject_ids": ids,
        "subject_count": count,
        "subject_truncated": truncated,
    }


def _parse_request(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"request_method": None, "request_path": None, "request_query_keys": None}
    if not isinstance(raw, dict):
        raise _Reject("INVALID_FIELD", "request must be an object")

    method = raw.get("method")
    if method is not None and method not in HTTP_METHODS:
        raise _Reject("INVALID_FIELD", "request.method is not a valid HTTP method")

    path = raw.get("path")
    if path is not None:
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 255:
            raise _Reject("INVALID_FIELD", "request.path must be an absolute path (<=255)")
        if "?" in path or "#" in path:
            raise _Reject("INVALID_FIELD", "request.path must not include a query string")

    keys = raw.get("query_keys")
    if keys is not None:
        if not isinstance(keys, list) or len(keys) > 50:
            raise _Reject("INVALID_FIELD", "request.query_keys must be an array (<=50)")
        if not all(isinstance(k, str) and _QUERY_KEY.fullmatch(k) for k in keys):
            raise _Reject("INVALID_FIELD", "request.query_keys must contain key names only")

    return {"request_method": method, "request_path": path, "request_query_keys": keys}


def _parse_context(raw: Any, action: str) -> dict[str, Any] | None:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _Reject("INVALID_FIELD", "context must be an object")
    unknown = raw.keys() - CONTEXT_KEYS
    if unknown:
        raise _Reject("UNKNOWN_CONTEXT_KEY", f"unknown context keys: {sorted(unknown)[:5]}")

    def check(key: str, ok: bool, expected: str) -> None:
        if key in raw and not ok:
            raise _Reject("INVALID_FIELD", f"context.{key} must be {expected}")

    reason, ticket, query = raw.get("reason"), raw.get("ticket_id"), raw.get("query")
    check("reason", isinstance(reason, str) and 0 < len(reason) <= 500, "a string (1-500)")
    check("ticket_id", isinstance(ticket, str) and 0 < len(ticket) <= 64, "a string (1-64)")
    check("report_id", _is_int(raw.get("report_id")), "an integer")
    check("query", isinstance(query, str) and len(query) <= 10_000, "a string")
    check("row_count", _is_int(raw.get("row_count")) and raw["row_count"] >= 0, "an integer")
    target = raw.get("target")
    check(
        "target",
        isinstance(target, dict)
        and len(target) == 1
        and next(iter(target)) in ("detection_id", "access_log_id")
        and _is_int(next(iter(target.values()))),
        '{"detection_id": int} or {"access_log_id": int}',
    )

    if action == "UNMASK" and "reason" not in raw:
        raise _Reject("REASON_REQUIRED", "context.reason is required for UNMASK")  # §12①
    return raw or None


def validate_event(raw: Any, now: datetime, *, internal: bool = False) -> dict | Rejection:
    """통과하면 access_log 컬럼 dict(source_system_id 제외), 아니면 Rejection.

    internal=True는 Argus 자체 기록(LOG-17)용 — EXPORT·UNMASK·ACCESS_LOG를 허용한다.
    """
    if not isinstance(raw, dict):
        return Rejection(None, "INVALID_FIELD", "event must be an object")

    raw_event_id = raw.get("event_id")
    shown_id = raw_event_id if isinstance(raw_event_id, str) and len(raw_event_id) <= 64 else None
    try:
        event_id = _require(raw, "event_id")
        if not isinstance(event_id, str) or not _UUID.fullmatch(event_id):
            raise _Reject("INVALID_FIELD", "event_id must be a UUID")

        for key in ("occurred_at", "client_ip", "action", "access_path", "data_category", "result"):
            _require(raw, key)
        actor = raw.get("actor")
        if not isinstance(actor, dict) or actor.get("login_id") in (None, ""):
            raise _Reject("MISSING_FIELD", "actor.login_id is required")

        action = _code(
            raw["action"],
            EXTERNAL_ACTIONS,
            ARGUS_ONLY_ACTIONS,
            internal,
            "INVALID_ACTION",
            "action",
        )
        category = _code(
            raw["data_category"],
            EXTERNAL_CATEGORIES,
            ARGUS_ONLY_CATEGORIES,
            internal,
            "INVALID_CATEGORY",
            "data_category",
        )
        access_path = _code(
            raw["access_path"],
            ACCESS_PATHS,
            frozenset(),
            internal,
            "INVALID_ACCESS_PATH",
            "access_path",
        )
        if raw["result"] not in RESULTS:
            raise _Reject("INVALID_FIELD", "result must be SUCCESS or FAILURE")

        occurred_at = _parse_timestamp(raw["occurred_at"], now)
        client_ip = _parse_ip(raw["client_ip"])

        login_id = actor["login_id"]
        if not isinstance(login_id, str) or not _LOGIN_ID.fullmatch(login_id):
            raise _Reject("INVALID_FIELD", "actor.login_id must be 1-64 printable characters")

        entry = {
            "event_id": uuid.UUID(event_id),
            "access_path": access_path,
            "actor_login_id": login_id,
            "occurred_at": occurred_at,
            "client_ip": client_ip,
            "action": action,
            "data_category": category,
            "result": raw["result"],
            **_parse_subject(raw.get("subject"), action),
            **_parse_request(raw.get("request")),
            "context": _parse_context(raw.get("context"), action),
        }
    except _Reject as reject:
        return Rejection(shown_id, reject.code, reject.message)
    return entry
