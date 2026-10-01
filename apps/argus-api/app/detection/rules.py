"""탐지 룰 형식 검사와 조건식 판정 (db-schema 3-6, policy 1-2)

형식 검사(validate_rule)와 판정(matches)을 나눈다.
검사기는 하나만 두어 탐지 배치(순찰 전 이중 확인)와 나중의 룰 빌더 API(저장 시 거부)가 함께 쓴다
— 규칙이 한 곳에만 존재하도록.

"잘못된 룰"은 사람의 판단이 아니라 **이 코드가 해석할 수 없는 룰**이다:
모르는 필드·연산자, 값 타입 불일치, 구조 오류, 아직 지원하지 않는 유형(AGGREGATE).
기준값이 이상한 룰(건수 10 등)은 형식상 정상이다 — 그 판단은 담당자의 몫.

조건식:
    {"all": [조건, ...]} (AND) / {"any": [조건, ...]} (OR), 1단계 중첩 허용
    조건 = {"field": ..., "op": ..., "value": ...}

조건식은 접속기록 컬럼과 그로부터 계산한 값(evaluation_facts)을 본다:
- occurred_time·occurred_weekday: 발생 시각의 **한국 시각(KST)** 시:분·요일 (db-schema 3-7 v0.4)
- actor_terminated_at_or_before: 행위 시점에 이미 퇴직했는가 — 현재 재직상태가 아니라
  `handler.terminated_at <= occurred_at` (policy 1-3: 퇴직 전 정상 접속의 소급 탐지·늦게 도착한
  기록의 오판정 방지). **명부에 없는 계정은 판정 불가 → 참으로 본다**(2026-10-01 사용자 결정):
  판정 불가를 "이상 없음"으로 넘기면 탐지 누락이고, relay가 명부를 기록보다 먼저 보내므로
  정상이라면 생기지 않는다 — 생겼다면 동기화 실패나 명부 밖 계정이라 그 자체가 점검 대상
actor_team(db-schema 3-6)은 아직 쓰는 룰이 없어 넣지 않았다 — 룰 빌더(기능 레이어 6) 때 추가.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))  # 판정 기준 시간대 — 한국은 서머타임이 없어 고정 오프셋
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")  # datetime.weekday() 순서
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class RuleError(ValueError):
    """룰을 해석할 수 없음 — 배치는 이 룰을 건너뛰지 않고 순찰 전체를 멈춘다 (2026-10-01 결정)"""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(v, str) for v in value)


def _is_time_range(value: Any) -> bool:
    # ["22:00", "06:00"] — 시작 포함·끝 미포함, 시작 > 끝이면 자정을 넘긴다. 같으면 뜻이 모호해 거부
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, str) and _HHMM.match(v) for v in value)
        and value[0] != value[1]
    )


def _is_weekday_list(value: Any) -> bool:
    return _is_str_list(value) and all(v in WEEKDAYS for v in value)


def _is_true(value: Any) -> bool:
    # "행위 시점에 퇴직하지 않았음"(false)은 명부 미등록을 어떻게 볼지 모호해 받지 않는다
    return value is True


@dataclass(frozen=True)
class _Field:
    key: str  # evaluation_facts의 키 (접속기록 컬럼 또는 계산한 값)
    ops: Mapping[str, Callable[[Any], bool]]  # 연산자 → 값 형식 검사


FIELDS: Mapping[str, _Field] = {
    "action": _Field("action", {"eq": _is_str, "in": _is_str_list}),
    "data_category": _Field("data_category", {"eq": _is_str, "in": _is_str_list}),
    "result": _Field("result", {"eq": _is_str}),
    "subject_count": _Field("subject_count", {"gte": _is_int, "lte": _is_int, "eq": _is_int}),
    "occurred_time": _Field("occurred_time", {"between": _is_time_range}),
    "occurred_weekday": _Field("occurred_weekday", {"in": _is_weekday_list}),
    "actor_terminated_at_or_before": _Field("actor_terminated_at_or_before", {"eq": _is_true}),
}
# 취급자 명부(handler)를 봐야 판정할 수 있는 필드
ROSTER_FIELDS = frozenset({"actor_terminated_at_or_before"})

SUPPORTED_RULE_TYPES = frozenset({"EVENT", "AGGREGATE"})
# 기본 그룹핑 (policy 2-2) — EVENT는 (취급자, 룰, KST 날짜), AGGREGATE는 (취급자, 룰, 윈도우)
SUPPORTED_GROUP_BY = frozenset({"ACTOR_RULE_DATE"})

# AGGREGATE 집계 스펙 (db-schema 3-6) — 윈도우는 한국 시각 기준의 **고정** 구간(매시 정각·매일 0시·
# 매월 1일 0시). 정책 예시 "cs_kim의 9/15 14:00~15:00 대량조회 = 1건"이 고정 구간이다
WINDOWS = frozenset({"1h", "1d", "1mo"})
MEASURES = frozenset({"LOG_COUNT", "SUBJECT_COUNT", "DISTINCT_SUBJECT"})
BASELINES = frozenset({"PREV_MONTH_SAME_PERIOD"})


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _validate_aggregate(spec: Any) -> None:
    if not isinstance(spec, dict):
        raise RuleError("aggregate must be an object")
    compare = spec.get("compare")
    if compare == "ABSOLUTE":
        expected = {"window", "measure", "compare", "threshold"}
        if not (_is_int(spec.get("threshold")) and spec["threshold"] > 0):
            raise RuleError("ABSOLUTE threshold must be a positive integer")
    elif compare == "RATIO_TO_BASELINE":
        expected = {"window", "measure", "compare", "baseline", "threshold"}
        if "min_baseline" in spec:
            # 기준선이 이보다 작으면 판정하지 않는다 — 월초·휴일 직후처럼 기준선이 몇 건뿐이면
            # 비율이 쉽게 튄다(2026-10-01, 시드 1년치 검사에서 발견 — 사용자 확인 대기)
            expected.add("min_baseline")
            if not (_is_int(spec["min_baseline"]) and spec["min_baseline"] >= 1):
                raise RuleError("min_baseline must be a positive integer")
        if spec.get("baseline") not in BASELINES:
            raise RuleError(f"unsupported baseline: {spec.get('baseline')!r}")
        if spec.get("window") != "1mo":  # 전월 동기 비교는 월 윈도우에서만 뜻이 있다
            raise RuleError("PREV_MONTH_SAME_PERIOD needs window 1mo")
        if not _is_positive_number(spec.get("threshold")):
            raise RuleError("ratio threshold must be a positive number")
    else:
        raise RuleError(f"unsupported compare: {compare!r}")
    if set(spec) != expected:
        raise RuleError(f"aggregate keys must be {sorted(expected)}")
    if spec["window"] not in WINDOWS:
        raise RuleError(f"unsupported window: {spec['window']!r}")
    if spec["measure"] not in MEASURES:
        raise RuleError(f"unsupported measure: {spec['measure']!r}")


ACCESS_PATHS = frozenset({"APP", "DB", "ALL"})
_GROUP_KEYS = ("all", "any")


def _validate_leaf(leaf: Any) -> None:
    if not isinstance(leaf, dict) or set(leaf) != {"field", "op", "value"}:
        raise RuleError("condition must be {field, op, value}")
    field = FIELDS.get(leaf["field"])
    if field is None:
        raise RuleError(f"unsupported field: {leaf['field']!r}")
    value_ok = field.ops.get(leaf["op"])
    if value_ok is None:
        raise RuleError(f"unsupported op {leaf['op']!r} for field {leaf['field']!r}")
    if not value_ok(leaf["value"]):
        raise RuleError(f"invalid value for {leaf['field']} {leaf['op']}")


def _validate_group(group: Any, depth: int) -> None:
    if not isinstance(group, dict) or len(group) != 1 or next(iter(group)) not in _GROUP_KEYS:
        raise RuleError('condition group must be {"all": [...]} or {"any": [...]}')
    items = next(iter(group.values()))
    if not isinstance(items, list) or not items:
        raise RuleError("condition group must have at least one condition")
    for item in items:
        if isinstance(item, dict) and len(item) == 1 and next(iter(item)) in _GROUP_KEYS:
            if depth >= 1:
                raise RuleError("condition groups may nest only one level")
            _validate_group(item, depth + 1)
        else:
            _validate_leaf(item)


def validate_rule(rule: Mapping[str, Any]) -> None:
    """해석할 수 없는 룰이면 RuleError"""
    if rule["rule_type"] not in SUPPORTED_RULE_TYPES:
        raise RuleError(f"unsupported rule_type: {rule['rule_type']}")
    if rule["group_by"] not in SUPPORTED_GROUP_BY:
        raise RuleError(f"unsupported group_by: {rule['group_by']}")
    if rule["access_path"] not in ACCESS_PATHS:
        raise RuleError(f"unsupported access_path: {rule['access_path']}")
    _validate_group(rule["condition"], depth=0)
    if rule["rule_type"] == "AGGREGATE":
        _validate_aggregate(rule.get("aggregate"))
    elif rule.get("aggregate") is not None:
        raise RuleError("EVENT rule must not have aggregate")


def applies_to_path(rule_access_path: str, log_access_path: str) -> bool:
    # 접근 경로는 조건식이 아니라 룰의 독립 컬럼 — 경로마다 정상 기준선이 반대라서 (policy 1-2)
    return rule_access_path in ("ALL", log_access_path)


def uses_fields(condition: Mapping[str, Any], names: frozenset[str]) -> bool:
    """조건식이 names 중 하나라도 쓰는가 (검증된 조건식)"""
    (_, items) = next(iter(condition.items()))
    return any(
        uses_fields(item, names) if next(iter(item)) in _GROUP_KEYS else item["field"] in names
        for item in items
    )


def evaluation_facts(
    log: Mapping[str, Any], roster: Mapping[tuple[int, str], datetime | None]
) -> dict[str, Any]:
    """룰이 보는 값 — 접속기록 컬럼 + 계산한 값

    roster: (출처, 계정) → 퇴직 시각(재직 중이면 None). 키가 없으면 명부 미등록.
    """
    occurred_at: datetime = log["occurred_at"]
    local = occurred_at.astimezone(KST)
    key = (log["source_system_id"], log["actor_login_id"])
    registered = key in roster
    terminated_at = roster.get(key)
    return dict(log) | {
        "occurred_time": local.strftime("%H:%M"),
        "occurred_weekday": WEEKDAYS[local.weekday()],
        "actor_registered": registered,
        # 명부 미등록 = 판정 불가 → 참 (모듈 설명 참고)
        "actor_terminated_at_or_before": (
            not registered or (terminated_at is not None and terminated_at <= occurred_at)
        ),
    }


def _in_time_range(actual: str, start: str, end: str) -> bool:
    # "HH:MM" 0 채움 문자열이라 사전순 비교 = 시각 비교. 분 단위로 잘라 [시작, 끝)
    if start < end:
        return start <= actual < end
    return actual >= start or actual < end  # 자정 넘김 (22:00~06:00)


def _leaf_matches(leaf: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    actual = facts[FIELDS[leaf["field"]].key]
    expected = leaf["value"]
    match leaf["op"]:
        case "eq":
            return actual == expected
        case "in":
            return actual in expected
        case "gte":
            return actual >= expected
        case "lte":
            return actual <= expected
        case "between":
            return _in_time_range(actual, *expected)
    raise RuleError(f"unsupported op: {leaf['op']}")  # validate_rule을 거쳤다면 도달하지 않음


def matches(condition: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    """검증된 조건식을 접속기록 1건(evaluation_facts)에 적용 (EVENT 룰)"""
    (kind, items) = next(iter(condition.items()))
    results = (
        matches(item, facts) if next(iter(item)) in _GROUP_KEYS else _leaf_matches(item, facts)
        for item in items
    )
    return all(results) if kind == "all" else any(results)
