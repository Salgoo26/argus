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

Walking Skeleton이 평가하는 필드는 아래 4개다. 야간·주말·퇴직자 계정 접속 룰에 필요한
occurred_time·occurred_weekday·actor_team·actor_terminated_at_or_before는 해당 룰을 넣을 때
FIELDS에 추가한다 (Skeleton 이후 단계).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class RuleError(ValueError):
    """룰을 해석할 수 없음 — 배치는 이 룰을 건너뛰지 않고 순찰 전체를 멈춘다 (2026-10-01 결정)"""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(v, str) for v in value)


@dataclass(frozen=True)
class _Field:
    column: str  # access_log 컬럼
    ops: Mapping[str, Callable[[Any], bool]]  # 연산자 → 값 형식 검사


FIELDS: Mapping[str, _Field] = {
    "action": _Field("action", {"eq": _is_str, "in": _is_str_list}),
    "data_category": _Field("data_category", {"eq": _is_str, "in": _is_str_list}),
    "result": _Field("result", {"eq": _is_str}),
    "subject_count": _Field("subject_count", {"gte": _is_int, "lte": _is_int, "eq": _is_int}),
}

SUPPORTED_RULE_TYPES = frozenset({"EVENT"})  # AGGREGATE는 v0.1 범위 밖 (CLAUDE.md 6절)
SUPPORTED_GROUP_BY = frozenset({"ACTOR_RULE_DATE"})  # policy 2-2 기본 그룹핑
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


def applies_to_path(rule_access_path: str, log_access_path: str) -> bool:
    # 접근 경로는 조건식이 아니라 룰의 독립 컬럼 — 경로마다 정상 기준선이 반대라서 (policy 1-2)
    return rule_access_path in ("ALL", log_access_path)


def _leaf_matches(leaf: Mapping[str, Any], log: Mapping[str, Any]) -> bool:
    actual = log[FIELDS[leaf["field"]].column]
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
    raise RuleError(f"unsupported op: {leaf['op']}")  # validate_rule을 거쳤다면 도달하지 않음


def matches(condition: Mapping[str, Any], log: Mapping[str, Any]) -> bool:
    """검증된 조건식을 접속기록 1건에 적용 (EVENT 룰)"""
    (kind, items) = next(iter(condition.items()))
    results = (
        matches(item, log) if next(iter(item)) in _GROUP_KEYS else _leaf_matches(item, log)
        for item in items
    )
    return all(results) if kind == "all" else any(results)
