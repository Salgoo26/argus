"""AGGREGATE 윈도우·기준선 계산 (기능 레이어 4, app/detection/aggregate.py)"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.detection.aggregate import (
    bucket_label,
    measure,
    prev_month_same_period,
    window_end,
    window_start,
)

KST = timezone(timedelta(hours=9))


def kst(*args) -> datetime:
    return datetime(*args, tzinfo=KST)


@pytest.mark.parametrize(
    ("window", "start", "end"),
    [
        ("1h", kst(2026, 9, 15, 14, 0), kst(2026, 9, 15, 15, 0)),
        ("1d", kst(2026, 9, 15), kst(2026, 9, 16)),
        ("1mo", kst(2026, 9, 1), kst(2026, 10, 1)),
    ],
)
def test_fixed_windows_in_korean_time(window, start, end):
    occurred = datetime(2026, 9, 15, 5, 41, 7, tzinfo=UTC)  # KST 14:41:07
    assert window_start(occurred, window) == start
    assert window_end(start, window) == end


def test_month_window_crosses_the_year():
    start = window_start(datetime(2026, 12, 31, 16, 0, tzinfo=UTC), "1mo")  # KST 1/1 01:00
    assert start == kst(2027, 1, 1)
    assert window_end(kst(2026, 12, 1), "1mo") == kst(2027, 1, 1)


def test_bucket_label_is_the_window_start():
    assert bucket_label(kst(2026, 9, 15, 14, 0)) == "2026-09-15T14:00+09:00"


def test_prev_month_same_period():
    # 10/15 13:30까지 ↔ 9/1 ~ 9/15 13:30
    assert prev_month_same_period(kst(2026, 10, 1), kst(2026, 10, 15, 13, 30)) == (
        kst(2026, 9, 1),
        kst(2026, 9, 15, 13, 30),
    )


def test_prev_month_is_clamped_when_shorter():
    # 3/31 12:00까지(30.5일) ↔ 2월(28일) 전체 — 전월 말을 넘지 않는다
    assert prev_month_same_period(kst(2026, 3, 1), kst(2026, 3, 31, 12, 0)) == (
        kst(2026, 2, 1),
        kst(2026, 3, 1),
    )


def test_measures():
    logs = [
        {"subject_count": 20, "subject_ids": ["1", "2"]},
        {"subject_count": 5, "subject_ids": ["2", "3"]},
    ]
    assert measure(logs, "LOG_COUNT") == 2
    assert measure(logs, "SUBJECT_COUNT") == 25
    assert measure(logs, "DISTINCT_SUBJECT") == 3
