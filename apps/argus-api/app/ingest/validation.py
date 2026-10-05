"""수집 이벤트 건별 검증 (api-spec 2-2, 2-3, 2-5)

형식 오류 1건 때문에 배치 전체를 거부하지 않도록 이벤트마다 판정하고(api-spec 1-4),
통과한 이벤트는 access_log 컬럼 dict로 바꿔 돌려준다.

원본 개인정보 차단 (CLAUDE.md 3절 #3):
- 정의되지 않은 필드는 무시한다(저장하지 않음, api-spec 6절 하위 호환)
- subject.ids는 내부 PK 형태만 허용 — 이메일·전화번호 같은 값이 섞여 들어오면 거부
- request.path에 쿼리스트링이 붙어 오면 거부 — 검색 조건 "값"이 개인정보일 수 있음
- DB 경로(2티어)는 정규화 SQL만 받는다 — 작은따옴표(문자열 리터럴)가 남아 있으면 거부
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
CONTEXT_KEYS = frozenset({"ticket_id", "reason", "target", "report_id"})
# DB 접근 게이트웨이(2티어) 전용 키 — access_path=DB일 때만 허용 (api-spec 2-3 v0.5)
# SQL 원문(구 `query`)은 받지 않는다 — 리터럴·매개변수에 개인정보가 실림 (원문은 게이트웨이에만)
DB_CONTEXT_KEYS = frozenset(
    {
        "db_user",
        "sql_normalized",
        "tables",
        "columns",
        "row_count",
        "raw_ref",
        "raw_fingerprint",
        "subject_unresolved",
        "token_id",
    }
)
DB_REQUIRED_KEYS = ("db_user", "raw_ref", "raw_fingerprint")
DB_REQUIRED_KEYS_UNLESS_LOGIN = ("sql_normalized", "row_count")

SUBJECT_IDS_LIMIT = 1000  # api-spec 2-2
FUTURE_TOLERANCE = timedelta(minutes=5)  # api-spec 2-5

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SUBJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # 내부 PK — '@', '.', 공백 등 불가
_LOGIN_ID = re.compile(r"^[\x21-\x7e]{1,64}$")  # 공백·제어문자 없는 출력 가능 ASCII
_QUERY_KEY = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,64}$")
_DB_USER = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_DB_OBJECT = re.compile(r'^[A-Za-z0-9_."]{1,128}$')  # 테이블·컬럼 이름 (스키마·별칭 포함)
_RAW_REF = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")  # $$·$tag$ (자리표시 $1은 제외)
SQL_NORMALIZED_MAX = 4000


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


def _string_list(value: Any, limit: int, pattern: re.Pattern) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= limit
        and all(isinstance(v, str) and pattern.fullmatch(v) for v in value)
    )


def _check_db_context(raw: dict, action: str, check) -> None:
    """2티어 기록 필드 (api-spec 2-2 "`access_path=DB` 기록 규칙", v0.5)"""
    required = DB_REQUIRED_KEYS
    if action != "LOGIN":
        required += DB_REQUIRED_KEYS_UNLESS_LOGIN
    for key in required:
        if raw.get(key) in (None, ""):
            raise _Reject("MISSING_FIELD", f"context.{key} is required for access_path DB")

    db_user, sql = raw.get("db_user"), raw.get("sql_normalized")
    check("db_user", isinstance(db_user, str) and _DB_USER.fullmatch(db_user), "a DB account name")
    check(
        "sql_normalized",
        isinstance(sql, str) and 0 < len(sql) <= SQL_NORMALIZED_MAX,
        f"a string (1-{SQL_NORMALIZED_MAX})",
    )
    # 두 번째 방어선: 정규화 SQL은 리터럴이 $n으로 바뀌어 따옴표·달러 인용($$…$$)이 남을 수 없다.
    # 남았다면 정규화 실패이거나 원문이 실린 것 — 값은 메시지에 되풀이하지 않는다 (절대 규칙 #3)
    if isinstance(sql, str) and ("'" in sql or _DOLLAR_QUOTE.search(sql)):
        raise _Reject("INVALID_FIELD", "context.sql_normalized must not contain string literals")
    check("tables", _string_list(raw.get("tables"), 50, _DB_OBJECT), "table names (<=50)")
    check("columns", _string_list(raw.get("columns"), 200, _DB_OBJECT), "column names (<=200)")
    check("row_count", _is_int(raw.get("row_count")) and raw["row_count"] >= 0, "an integer")
    raw_ref, fingerprint = raw.get("raw_ref"), raw.get("raw_fingerprint")
    check("raw_ref", isinstance(raw_ref, str) and _RAW_REF.fullmatch(raw_ref), "a raw reference")
    check(
        "raw_fingerprint",
        isinstance(fingerprint, str) and _FINGERPRINT.fullmatch(fingerprint),
        "sha256:<64 lowercase hex>",
    )
    check("subject_unresolved", isinstance(raw.get("subject_unresolved"), bool), "a boolean")
    token_id = raw.get("token_id")
    check("token_id", isinstance(token_id, str) and _UUID.fullmatch(token_id), "a UUID")


def _parse_context(raw: Any, action: str, access_path: str) -> dict[str, Any] | None:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _Reject("INVALID_FIELD", "context must be an object")
    allowed = CONTEXT_KEYS | DB_CONTEXT_KEYS if access_path == "DB" else CONTEXT_KEYS
    unknown = raw.keys() - allowed
    if unknown:
        raise _Reject("UNKNOWN_CONTEXT_KEY", f"unknown context keys: {sorted(unknown)[:5]}")

    def check(key: str, ok: bool, expected: str) -> None:
        if key in raw and not ok:
            raise _Reject("INVALID_FIELD", f"context.{key} must be {expected}")

    reason, ticket = raw.get("reason"), raw.get("ticket_id")
    check("reason", isinstance(reason, str) and 0 < len(reason) <= 500, "a string (1-500)")
    check("ticket_id", isinstance(ticket, str) and 0 < len(ticket) <= 64, "a string (1-64)")
    check("report_id", _is_int(raw.get("report_id")), "an integer")
    if access_path == "DB":
        _check_db_context(raw, action, check)
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
            "context": _parse_context(raw.get("context"), action, access_path),
        }
    except _Reject as reject:
        return Rejection(shown_id, reject.code, reject.message)
    return entry
