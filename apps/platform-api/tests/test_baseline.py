"""기준선용 과거 접속기록 — 모양, 다른 룰에 걸리지 않음, 전월 대비 비율 (기능 레이어 4)

Argus의 전월 대비 급증 판정(argus-api app/detection/aggregate.py)과 같은 정의로 비율을 계산해
"평소 취급자는 어느 날 시드해도 2배를 넘지 않는다"를 1년치 날짜로 확인한다.
"""

import random
from collections import Counter
from datetime import datetime, timedelta

import pytest

from app.scripts.baseline import (
    KST,
    PREV_MONTH_PER_DAY,
    SPIKE_ACTOR,
    SPIKE_PER_DAY,
    THIS_MONTH_PER_DAY,
    baseline_events,
)

FIRST_ID, MEMBERS = 10001, 500
MIN_BASELINE = 20  # argus-api 마이그레이션 0007의 전월 대비 급증 룰 min_baseline과 같은 값
NOW = datetime(2026, 10, 15, 13, 30, tzinfo=KST)  # 목요일 오후


def events_at(now: datetime, actors=("ops_park", SPIKE_ACTOR)) -> list[dict]:
    rng = random.Random(1)  # noqa: S311 — 가짜 기록의 시각·페이지 분산용, 보안 용도 아님
    return baseline_events(list(actors), FIRST_ID, MEMBERS, now, rng)


def at(event) -> datetime:
    return datetime.fromisoformat(event["occurred_at"]).astimezone(KST)


def weekdays(start: datetime, end: datetime) -> int:
    return sum((start + timedelta(days=d)).weekday() < 5 for d in range((end - start).days))


def test_events_are_weekday_office_hour_list_reads():
    events = events_at(NOW)
    assert events and [at(e) for e in events] == sorted(at(e) for e in events)  # 시간순
    for event in events:
        moment = at(event)
        # 야간·주말 룰에 걸리지 않게 — 평일 09:00~17:59
        assert moment.weekday() < 5 and 9 <= moment.hour < 18
        assert moment < NOW - timedelta(minutes=1)
        assert event["action"] == "READ" and event["data_category"] == "MEMBER_BASIC"
        assert event["request"] == {
            "method": "GET",
            "path": "/admin/members",
            "query_keys": ["page", "size"],
        }
        ids = [int(i) for i in event["subject"]["ids"]]
        assert 1 <= len(ids) <= 20 and event["subject"]["count"] == len(ids)
        assert FIRST_ID <= min(ids) and max(ids) < FIRST_ID + MEMBERS
        assert event["client_ip"].startswith("10.20.3.")


def test_counts_per_month():
    events = events_at(NOW)
    by_actor_month = Counter((e["actor"]["login_id"], at(e).month) for e in events)
    september = weekdays(datetime(2026, 9, 1, tzinfo=KST), datetime(2026, 10, 1, tzinfo=KST))
    # 10/1~10/14 평일 + 10/15(오늘) 중 13:29 이전 기록
    assert by_actor_month[("ops_park", 9)] == PREV_MONTH_PER_DAY * september
    assert THIS_MONTH_PER_DAY * 10 <= by_actor_month[("ops_park", 10)] <= THIS_MONTH_PER_DAY * 11
    assert SPIKE_PER_DAY * 10 <= by_actor_month[(SPIKE_ACTOR, 10)] <= SPIKE_PER_DAY * 11
    assert {month for _, month in by_actor_month} == {9, 10}  # 지지난달 이전은 없다


def test_no_hour_reaches_the_bulk_read_threshold():
    per_hour = Counter(
        (e["actor"]["login_id"], at(e).replace(minute=0, second=0, microsecond=0))
        for e in events_at(NOW)
    )
    assert max(per_hour.values()) < 100  # 대량 조회(1시간 100건) 룰에 걸리지 않는다


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _ratio(events, actor: str, now: datetime) -> float | None:
    """argus-api의 전월 대비 급증 판정과 같은 정의 (prev_month_same_period, min_baseline)"""
    this_start = _month_start(now)
    prev_start = _month_start(this_start - timedelta(days=1))
    prev_end = min(prev_start + (now - this_start), this_start)
    times = [at(e) for e in events if e["actor"]["login_id"] == actor]
    current = sum(this_start <= t < now for t in times)
    base = sum(prev_start <= t < prev_end for t in times)
    return None if base < MIN_BASELINE else current / base  # 기준선이 작으면 판정하지 않음


@pytest.mark.parametrize("hour", [8, 13, 19])
def test_ordinary_actor_never_reaches_double_on_any_day(hour):
    # 2026년 매일 — 평일 수가 달마다 달라도(월초 등) 평소 취급자는 전월 대비 2배 미만
    day = datetime(2026, 1, 1, hour, tzinfo=KST)
    while day.year == 2026:
        events = events_at(day, actors=("ops_park",))
        ratio = _ratio(events, "ops_park", day)
        assert ratio is None or ratio < 2.0, day
        day += timedelta(days=1)


def test_spike_actor_reaches_double_once_the_month_is_under_way():
    assert _ratio(events_at(NOW), SPIKE_ACTOR, NOW) >= 2.0
