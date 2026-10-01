"""탐지 룰 형식 검사·조건식 판정 (db-schema 3-6)"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.detection.rules import (
    RuleError,
    applies_to_path,
    evaluation_facts,
    matches,
    uses_fields,
    validate_rule,
)

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


# ── 시각·요일·퇴직 여부 (기능 레이어 1) ────────────────────

KST = timezone(timedelta(hours=9))
NIGHT = {"all": [{"field": "occurred_time", "op": "between", "value": ["22:00", "06:00"]}]}
OFFICE_HOURS = {"all": [{"field": "occurred_time", "op": "between", "value": ["09:00", "18:00"]}]}
WEEKEND = {"all": [{"field": "occurred_weekday", "op": "in", "value": ["SAT", "SUN"]}]}
TERMINATED = {"all": [{"field": "actor_terminated_at_or_before", "op": "eq", "value": True}]}


def facts_at(occurred_at: datetime, roster=None, actor="ops_park") -> dict:
    entry = log(occurred_at=occurred_at, source_system_id=1, actor_login_id=actor)
    return evaluation_facts(entry, {(1, "ops_park"): None} if roster is None else roster)


def test_facts_use_korean_time():
    facts = facts_at(datetime(2026, 10, 2, 15, 30, tzinfo=UTC))  # 금 15:30 UTC = 토 00:30 KST
    assert facts["occurred_time"] == "00:30" and facts["occurred_weekday"] == "SAT"


@pytest.mark.parametrize(
    ("hhmm", "night", "office"),
    [
        ((21, 59), False, False),
        ((22, 0), True, False),
        ((0, 0), True, False),
        ((5, 59), True, False),
        ((6, 0), False, False),
        ((9, 0), False, True),  # 시작 포함
        ((17, 59), False, True),
        ((18, 0), False, False),  # 끝 미포함
    ],
)
def test_time_range_includes_start_excludes_end(hhmm, night, office):
    facts = facts_at(datetime(2026, 9, 29, *hhmm, tzinfo=KST))
    assert matches(NIGHT, facts) is night
    assert matches(OFFICE_HOURS, facts) is office


def test_weekday_condition():
    assert matches(WEEKEND, facts_at(datetime(2026, 10, 4, 12, 0, tzinfo=KST)))  # 일
    assert not matches(WEEKEND, facts_at(datetime(2026, 10, 5, 12, 0, tzinfo=KST)))  # 월


def test_terminated_is_judged_at_the_time_of_access():
    terminated_at = datetime(2026, 9, 30, 18, 0, tzinfo=KST)
    roster = {(1, "ops_park"): terminated_at}
    assert not matches(TERMINATED, facts_at(terminated_at - timedelta(seconds=1), roster))
    assert matches(TERMINATED, facts_at(terminated_at, roster))  # 퇴직 시각 포함
    assert not matches(TERMINATED, facts_at(terminated_at, {(1, "ops_park"): None}))  # 재직 중


def test_actor_missing_from_roster_counts_as_terminated():
    facts = facts_at(datetime(2026, 9, 30, 10, 0, tzinfo=KST), roster={}, actor="ghost_kim")
    assert facts["actor_registered"] is False
    assert matches(TERMINATED, facts)  # 판정 불가 → 탐지 (2026-10-01 사용자 결정)


def test_uses_fields_sees_nested_conditions():
    nested = {"any": [{"all": TERMINATED["all"]}, BULK_DOWNLOAD["all"][0]]}
    assert uses_fields(nested, frozenset({"actor_terminated_at_or_before"}))
    assert not uses_fields(BULK_DOWNLOAD, frozenset({"actor_terminated_at_or_before"}))


@pytest.mark.parametrize(
    "bad",
    [
        rule({"all": [{"field": "occurred_time", "op": "between", "value": ["22:00"]}]}),
        rule({"all": [{"field": "occurred_time", "op": "between", "value": ["24:00", "06:00"]}]}),
        rule({"all": [{"field": "occurred_time", "op": "between", "value": ["9:00", "18:00"]}]}),
        rule({"all": [{"field": "occurred_time", "op": "between", "value": ["06:00", "06:00"]}]}),
        rule({"all": [{"field": "occurred_time", "op": "eq", "value": "22:00"}]}),
        rule({"all": [{"field": "occurred_weekday", "op": "in", "value": ["SATURDAY"]}]}),
        rule({"all": [{"field": "occurred_weekday", "op": "in", "value": []}]}),
        rule({"all": [{"field": "actor_terminated_at_or_before", "op": "eq", "value": False}]}),
        rule({"all": [{"field": "actor_terminated_at_or_before", "op": "eq", "value": 1}]}),
    ],
    ids=[
        "one-bound",
        "24h",
        "no-zero-pad",
        "same-bounds",
        "time-eq",
        "weekday-name",
        "no-weekday",
        "terminated-false",
        "terminated-int",
    ],
)
def test_unevaluable_time_and_actor_conditions(bad):
    with pytest.raises(RuleError):
        validate_rule(bad)
