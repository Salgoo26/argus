"""탐지 룰 형식 검사·조건식 판정 (db-schema 3-6)"""

import pytest

from app.detection.rules import RuleError, applies_to_path, matches, validate_rule

BULK_DOWNLOAD = {
    "all": [
        {"field": "action", "op": "eq", "value": "DOWNLOAD"},
        {"field": "subject_count", "op": "gte", "value": 50},
    ]
}


def rule(condition=BULK_DOWNLOAD, **overrides) -> dict:
    return {
        "rule_type": "EVENT",
        "group_by": "ACTOR_RULE_DATE",
        "access_path": "APP",
        "condition": condition,
    } | overrides


def log(**overrides) -> dict:
    return {
        "action": "DOWNLOAD",
        "data_category": "MEMBER_BASIC",
        "result": "SUCCESS",
        "subject_count": 120,
    } | overrides


# ── 판정 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (log(), True),
        (log(subject_count=50), True),  # 경계값 포함 (≥ 50)
        (log(subject_count=49), False),
        (log(action="READ"), False),
    ],
)
def test_bulk_download_condition(entry, expected):
    assert matches(BULK_DOWNLOAD, entry) is expected


def test_any_and_one_level_nesting():
    condition = {
        "any": [
            {"field": "data_category", "op": "eq", "value": "PAYMENT"},
            {
                "all": [
                    {"field": "action", "op": "in", "value": ["READ", "DOWNLOAD"]},
                    {"field": "result", "op": "eq", "value": "FAILURE"},
                ]
            },
        ]
    }
    validate_rule(rule(condition))
    assert matches(condition, log(data_category="PAYMENT"))
    assert matches(condition, log(action="READ", result="FAILURE"))
    assert not matches(condition, log(action="UPDATE", result="FAILURE"))


def test_lte_and_eq_on_count():
    condition = {"all": [{"field": "subject_count", "op": "lte", "value": 1}]}
    assert matches(condition, log(subject_count=1))
    assert not matches(condition, log(subject_count=2))


@pytest.mark.parametrize(
    ("rule_path", "log_path", "expected"),
    [("APP", "APP", True), ("APP", "DB", False), ("DB", "DB", True), ("ALL", "DB", True)],
)
def test_access_path_is_a_separate_filter(rule_path, log_path, expected):
    assert applies_to_path(rule_path, log_path) is expected


# ── 형식 검사: "해석할 수 없는 룰" ─────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        rule({"all": [{"field": "actor_mood", "op": "eq", "value": "x"}]}),  # 모르는 필드
        rule({"all": [{"field": "action", "op": "roughly", "value": "READ"}]}),  # 모르는 연산자
        rule({"all": [{"field": "subject_count", "op": "gte", "value": "많이"}]}),  # 값 타입
        rule({"all": [{"field": "subject_count", "op": "gte", "value": True}]}),  # bool은 숫자 아님
        rule({"all": [{"field": "action", "op": "in", "value": []}]}),  # 빈 목록
        rule({"all": [{"field": "action", "op": "eq"}]}),  # value 누락
        rule({"all": []}),  # 빈 그룹
        rule({"field": "action", "op": "eq", "value": "READ"}),  # all/any 없음
        rule({"all": [{"any": [{"all": [{"field": "action", "op": "eq", "value": "X"}]}]}]}),
        rule(rule_type="AGGREGATE"),  # v0.1 범위 밖
        rule(group_by="ACTOR_ONLY"),
        rule(access_path="WEB"),
    ],
    ids=[
        "unknown-field",
        "unknown-op",
        "string-count",
        "bool-count",
        "empty-in",
        "missing-value",
        "empty-group",
        "no-group",
        "nested-twice",
        "aggregate",
        "group-by",
        "access-path",
    ],
)
def test_unevaluable_rules_are_rejected(bad):
    with pytest.raises(RuleError):
        validate_rule(bad)


def test_seed_rule_shape_is_valid():
    validate_rule(rule())  # 마이그레이션 0004의 대량 다운로드 룰과 같은 조건식
