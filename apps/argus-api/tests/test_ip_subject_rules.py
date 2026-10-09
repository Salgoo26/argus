"""접속지(IP)·특정 정보주체 기반 탐지 (v0.1 보강 K — 갭 A10, 안내서 95)

K-1 룰 빌더 조건: client_ip in_cidr·not_in_cidr, 집계 DISTINCT_IP·MAX_SUBJECT_REPEAT
K-2 기본 룰: 허용 범위 밖 접속지 / 짧은 시간 여러 접속지 / 특정 회원 반복 처리
"""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.detection.aggregate import measure, most_repeated_subject
from app.detection.batch import run_batch
from app.detection.rules import FIELDS, MEASURES, RuleError, matches, validate_rule
from app.ledger.append import append_access_logs
from app.models import argus_user, detection, detection_status_history

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

KST = timezone(timedelta(hours=9))
DAY = datetime(2026, 10, 6, 10, 0, tzinfo=KST)  # 화요일 낮 — 야간·주말 룰과 겹치지 않게
PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]
WEB_RULES = Path(__file__).resolve().parents[2] / "argus-web" / "lib" / "rules.ts"


def rule(condition: dict, aggregate: dict | None = None) -> dict:
    return {
        "rule_type": "AGGREGATE" if aggregate else "EVENT",
        "group_by": "ACTOR_RULE_DATE",
        "access_path": "ALL",
        "condition": condition,
        "aggregate": aggregate,
    }


def ip_leaf(op: str, value) -> dict:
    return {"all": [{"field": "client_ip", "op": op, "value": value}]}


# ── K-1 조건·집계 ─────────────────────────────────────────


@pytest.mark.parametrize("op", ["in_cidr", "not_in_cidr"])
def test_cidr_conditions_are_valid(op):
    validate_rule(rule(ip_leaf(op, PRIVATE + ["2001:db8::/32", "203.0.113.7"])))


@pytest.mark.parametrize(
    "value",
    [[], ["10.0.0.0/33"], ["not-an-ip"], "10.0.0.0/8", [f"10.0.{n}.0/24" for n in range(51)]],
)
def test_bad_cidr_lists_are_rejected(value):
    with pytest.raises(RuleError):
        validate_rule(rule(ip_leaf("not_in_cidr", value)))


def test_cidr_ops_only_for_client_ip():
    with pytest.raises(RuleError):
        validate_rule(rule({"all": [{"field": "action", "op": "in_cidr", "value": PRIVATE}]}))


@pytest.mark.parametrize(
    ("ip", "outside"),
    [
        ("10.20.3.55", False),
        ("172.31.255.1", False),
        ("172.32.0.1", True),
        ("192.168.0.9", False),
        ("127.0.0.1", False),
        ("203.0.113.7", True),
        ("2001:db8::1", True),  # IPv6는 IPv4 대역 어디에도 속하지 않는다 → 밖
    ],
)
def test_not_in_cidr_matches_outside_addresses(ip, outside):
    facts = {"client_ip": ip}
    assert matches(ip_leaf("not_in_cidr", PRIVATE), facts) is outside
    assert matches(ip_leaf("in_cidr", PRIVATE), facts) is not outside


def test_distinct_ip_and_max_subject_repeat():
    logs = [
        {"client_ip": "10.0.0.1", "subject_type": "MEMBER", "subject_ids": ["1", "2"]},
        {"client_ip": "10.0.0.2", "subject_type": "MEMBER", "subject_ids": ["2"]},
        {"client_ip": "10.0.0.1", "subject_type": "MEMBER", "subject_ids": ["2", "3"]},
        {"client_ip": "10.0.0.3", "subject_type": "MEMBER", "subject_ids": []},  # 미특정
    ]
    assert measure(logs, "DISTINCT_IP") == 3
    assert measure(logs, "MAX_SUBJECT_REPEAT") == 3
    assert most_repeated_subject(logs) == (("MEMBER", "2"), 3)
    assert measure([{"subject_type": "MEMBER", "subject_ids": []}], "MAX_SUBJECT_REPEAT") == 0


def test_tie_picks_the_smaller_id():
    logs = [{"subject_type": "MEMBER", "subject_ids": ["20", "10"]}]
    assert most_repeated_subject(logs) == (("MEMBER", "10"), 1)


@pytest.mark.parametrize("kind", ["DISTINCT_IP", "MAX_SUBJECT_REPEAT"])
def test_new_measures_are_valid_aggregates(kind):
    spec = {"window": "1h", "measure": kind, "compare": "ABSOLUTE", "threshold": 3}
    validate_rule(rule({"all": [{"field": "result", "op": "eq", "value": "SUCCESS"}]}, spec))


@pytest.mark.skipif(not WEB_RULES.exists(), reason="argus-web 소스가 없는 실행 환경(컨테이너)")
def test_rule_builder_offers_the_same_fields_and_measures():
    # 화면 룰 빌더(lib/rules.ts)의 선택지가 서버 목록과 같아야 한다 — 한쪽에만 있으면 저장이
    # 거부되거나
    # 서버가 아는 조건을 화면에서 고를 수 없다. CI는 레포 전체가 있어 실행된다
    source = WEB_RULES.read_text(encoding="utf-8")
    fields_block = source.split("export const FIELDS = [", 1)[1].split("] as const", 1)[0]
    web_fields = set(re.findall(r'key: "([a-z_]+)"', fields_block))
    measures_block = source.split("export const MEASURE_LABELS = {", 1)[1].split("};", 1)[0]
    web_measures = set(re.findall(r"^\s*([A-Z_]+):", measures_block, re.MULTILINE))
    assert web_fields == set(FIELDS)
    assert web_measures == set(MEASURES)


# ── K-2 기본 룰 (순찰) ────────────────────────────────────


@pytest.fixture
def clean(admin_engine, seed_rules):
    yield
    with admin_engine.begin() as conn:
        for table in (
            "notification",
            "detection_log",
            "detection_status_history",
            "explanation",
            "detection",
            "detection_batch_run",
        ):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))


def use(admin_engine, seed_rules, name: str) -> None:
    reset_rules(admin_engine, seed_rules, enabled=(name,))


def append(app_engine, *entries: dict) -> None:
    with app_engine.begin() as conn:
        append_access_logs(conn, [make_entry(**e) for e in entries], received_at=datetime.now(UTC))


def cases(app_engine) -> list[dict]:
    with app_engine.connect() as conn:
        return [
            dict(r) for r in conn.execute(select(detection).order_by(detection.c.id)).mappings()
        ]


def test_seeded_rules(seed_rules):
    rows = {r["name"]: r for r in seed_rules}
    outside = rows["허용 범위 밖 접속지"]
    assert (outside["rule_type"], outside["access_path"], outside["severity"]) == (
        "EVENT",
        "ALL",
        "HIGH",
    )
    assert outside["condition"]["all"][0] == {
        "field": "client_ip",
        "op": "not_in_cidr",
        "value": PRIVATE,
    }
    assert rows["짧은 시간 여러 접속지"]["aggregate"] == {
        "window": "1h",
        "measure": "DISTINCT_IP",
        "compare": "ABSOLUTE",
        "threshold": 3,
    }
    repeat = rows["특정 회원 반복 처리"]
    assert repeat["access_path"] == "APP"
    assert repeat["aggregate"]["measure"] == "MAX_SUBJECT_REPEAT"
    assert repeat["aggregate"]["threshold"] == 20


def test_access_from_outside_the_allowed_range(app_engine, admin_engine, seed_rules, clean):
    use(admin_engine, seed_rules, "허용 범위 밖 접속지")
    append(
        app_engine,
        {"occurred_at": DAY, "client_ip": "10.20.3.55"},  # 사내망
        {"occurred_at": DAY, "client_ip": "203.0.113.7"},  # 밖 — 화면 경유
        {  # 밖 — DB 직접(게이트웨이가 받은 실제 접속지)
            "occurred_at": DAY,
            "client_ip": "198.51.100.4",
            "access_path": "DB",
            "subject_ids": [],
            "subject_count": 0,
        },
    )
    run_batch(app_engine)
    found = cases(app_engine)
    assert sorted(c["access_path"] for c in found) == ["APP", "DB"]
    assert all(c["severity"] == "HIGH" for c in found)


def test_many_addresses_within_an_hour(app_engine, admin_engine, seed_rules, clean):
    use(admin_engine, seed_rules, "짧은 시간 여러 접속지")
    append(
        app_engine,
        *[
            {"occurred_at": DAY + timedelta(minutes=n), "client_ip": f"10.20.3.{n}"}
            for n in (1, 2, 2)
        ],
    )
    run_batch(app_engine)
    assert cases(app_engine) == []  # 두 곳 — 기준 미만

    append(app_engine, {"occurred_at": DAY + timedelta(minutes=30), "client_ip": "10.20.9.9"})
    run_batch(app_engine)
    [case] = cases(app_engine)
    assert float(case["aggregate_value"]) == 3
    assert case["group_bucket"].startswith("2026-10-06T10:00")


def test_same_member_processed_repeatedly(app_engine, admin_engine, seed_rules, clean):
    use(admin_engine, seed_rules, "특정 회원 반복 처리")
    member = {"subject_ids": ["10293"], "subject_count": 1}
    append(
        app_engine,
        *[{"occurred_at": DAY + timedelta(minutes=n), **member} for n in range(19)],
        # 회원번호 없는 기록(미특정)은 세지 않는다
        *[
            {"occurred_at": DAY, "subject_ids": [], "subject_count": 5, "access_path": "DB"}
            for _ in range(5)
        ],
    )
    run_batch(app_engine)
    assert cases(app_engine) == []

    append(app_engine, {"occurred_at": DAY + timedelta(hours=2), **member})
    run_batch(app_engine)
    [case] = cases(app_engine)
    assert float(case["aggregate_value"]) == 20
    with app_engine.connect() as conn:
        comment = conn.execute(
            select(detection_status_history.c.comment).where(
                detection_status_history.c.detection_id == case["id"],
                detection_status_history.c.to_status == "DETECTED",
            )
        ).scalar_one()
    # 어느 회원인지는 마스킹 식별값으로만
    assert "member_10***" in comment and "10293" not in comment


# ── 룰 빌더 API ──────────────────────────────────────────


@pytest.fixture
def officer(app, admin_engine, seed_rules):
    password = "ip-rule-test-password-1"  # 테스트 전용 더미 값
    with admin_engine.begin() as conn:
        conn.execute(
            insert(argus_user).values(
                login_id="officer", password_hash=hash_password(password), role="OFFICER"
            )
        )
    c = TestClient(app, client=TEST_CLIENT_ADDR)
    assert (
        c.post("/api/auth/login", json={"login_id": "officer", "password": password}).status_code
        == 200
    )
    yield c
    c.close()
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM argus_user"))


def test_rule_builder_saves_ip_and_subject_rules(officer):
    base = {"access_path": "ALL", "severity": "HIGH"}
    outside = officer.post(
        "/api/rules",
        json=base
        | {
            "name": "본사 대역 밖 접속",
            "rule_type": "EVENT",
            "condition": ip_leaf("not_in_cidr", ["203.0.113.0/24"]),
        },
    )
    assert outside.status_code == 201, outside.text
    repeat = officer.post(
        "/api/rules",
        json=base
        | {
            "name": "회원 반복 조회 (1시간)",
            "rule_type": "AGGREGATE",
            "condition": {"all": [{"field": "action", "op": "eq", "value": "READ"}]},
            "aggregate": {
                "window": "1h",
                "measure": "MAX_SUBJECT_REPEAT",
                "compare": "ABSOLUTE",
                "threshold": 10,
            },
        },
    )
    assert repeat.status_code == 201, repeat.text
    bad = officer.post(
        "/api/rules",
        json=base
        | {
            "name": "잘못된 대역",
            "rule_type": "EVENT",
            "condition": ip_leaf("in_cidr", ["300.0.0.0/8"]),
        },
    )
    assert bad.status_code == 400
