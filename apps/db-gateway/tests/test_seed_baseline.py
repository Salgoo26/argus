"""DB 직접 접근 기준선 시드 — 모양, 다른 룰에 걸리지 않음, 전월 대비 비율, 한 번만 (③-3)

Argus의 전월 대비 급증 판정(argus-api app/detection/aggregate.py prev_month_same_period)과
같은 정의로 비율을 계산해 "평소 취급자는 어느 날 시드해도 2배를 넘지 않고, 급증 취급자는 넘는다"를
1년치 날짜로 확인한다.
"""

import calendar
import random
import re
from datetime import datetime, timedelta

import pytest

from app.seed_baseline import KST, SPIKE_ACTOR, baseline_records, baseline_schedule, seed
from app.store import fingerprint

MIN_BASELINE = 20  # argus-api 마이그레이션 0012 "DB 직접 전월 대비 급증"의 min_baseline
NOW = datetime(2026, 10, 15, 13, 30, tzinfo=KST)  # 목요일 오후


def rng() -> random.Random:
    return random.Random(1)  # noqa: S311 — 테스트용 고정 분산


def test_records_are_weekday_office_hour_db_reads():
    records = baseline_records(NOW, "platform_owner", rng())
    assert records
    for raw, event in records:
        moment = datetime.fromisoformat(event["occurred_at"]).astimezone(KST)
        # 야간·주말 룰에 걸리지 않게 — 평일 09:00~17:59
        assert moment.weekday() < 5 and 9 <= moment.hour < 18 and moment < NOW
        assert event["access_path"] == "DB" and event["action"] == "READ"
        assert event["data_category"] in {"MEMBER_BASIC", "ORDER", "INQUIRY"}
        context = event["context"]
        # 실제 중계와 같은 형식 — 정규화 SQL엔 값이 없다(회원번호 리터럴 → $1)
        assert "$1" in context["sql_normalized"]
        assert not re.search(r"\b1\d{4}\b", context["sql_normalized"])
        assert context["db_user"] == "platform_owner" and context["subject_unresolved"] is True
        assert event["subject"] == {"type": "MEMBER", "ids": [], "count": context["row_count"]}
        assert raw["seed"] is True and raw["event_id"] == event["event_id"]


def _prev_same_period(start: datetime, as_of: datetime) -> tuple[datetime, datetime]:
    prev_start = (start - timedelta(days=1)).replace(day=1)
    last_day = calendar.monthrange(prev_start.year, prev_start.month)[1]
    end = as_of.replace(year=prev_start.year, month=prev_start.month, day=min(as_of.day, last_day))
    return prev_start, end


def _ratio(now: datetime, actor: str) -> tuple[int, int]:
    times = [m for m, who, _ in baseline_schedule(now, rng()) if who == actor]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    b_start, b_end = _prev_same_period(start, now)
    current = sum(start <= t < now for t in times)
    base = sum(b_start <= t < b_end for t in times)
    return current, base


@pytest.mark.parametrize("day", range(0, 365, 3))
def test_only_the_spike_actor_doubles_whenever_judged(day):
    now = datetime(2026, 1, 1, 15, 0, tzinfo=KST) + timedelta(days=day)
    for actor in ("ops_park", SPIKE_ACTOR):
        current, base = _ratio(now, actor)
        if base < MIN_BASELINE:
            continue  # 월초 등 — 룰이 판정하지 않는다
        if actor == SPIKE_ACTOR:
            assert current >= 2 * base, (now, current, base)
        else:
            assert current < 2 * base, (now, current, base)


def test_seed_runs_once_and_keeps_raw_for_fingerprint_check(store):
    first = seed(store, NOW, "platform_owner", rng())
    assert first > 0 and seed(store, NOW, "platform_owner", rng()) == 0  # 다시 실행해도 그대로
    rows = store.outbox_rows()
    assert len(rows) == first and {r["status"] for r in rows} == {"PENDING"}
    event = rows[0]["payload"]
    raw, digest = store.raw(event["context"]["raw_ref"])
    assert digest == event["context"]["raw_fingerprint"] == fingerprint(raw)
