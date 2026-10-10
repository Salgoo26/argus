"""AGGREGATE 룰의 윈도우·기준선·집계값 (db-schema 3-6·3-7 #4, policy 1-2·2-2)

- 윈도우는 한국 시각 기준의 **고정 구간**: 1h = 매시 정각부터 1시간, 1d = 0시부터 하루,
  1mo = 1일 0시부터 한 달.
  정책 예시 "cs_kim의 9/15 14:00~15:00 대량조회 = 탐지건 1건"이 고정 구간이고, 탐지건의 그룹 키
  (group_bucket)도 윈도우 시작 시각이다
- 전월 동기(PREV_MONTH_SAME_PERIOD): 당월 1일부터 기준 시각까지 지난 만큼을 전월 1일부터 잰다.
  전월이 더 짧으면(3/31 ↔ 2월) 전월 말에서 자른다
- 판정은 언제나 occurred_at 기준 — 늦게 도착한 기록도 자기 윈도우를 다시 집계한다 (api-spec 2-6)
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from app.detection.rules import KST


def window_start(occurred_at: datetime, window: str) -> datetime:
    local = occurred_at.astimezone(KST)
    if window == "1h":
        return local.replace(minute=0, second=0, microsecond=0)
    if window == "1d":
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # 1mo


def _add_months(first_of_month: datetime, months: int) -> datetime:
    index = first_of_month.year * 12 + first_of_month.month - 1 + months
    return first_of_month.replace(year=index // 12, month=index % 12 + 1)


def window_end(start: datetime, window: str) -> datetime:
    if window == "1h":
        return start + timedelta(hours=1)
    if window == "1d":
        return start + timedelta(days=1)
    return _add_months(start, 1)


def bucket_label(start: datetime) -> str:
    """탐지건 그룹 키 — 윈도우 시작 시각(KST), 예: 2026-10-01T14:00+09:00"""
    return start.isoformat(timespec="minutes")


def prev_month_same_period(month_start: datetime, as_of: datetime) -> tuple[datetime, datetime]:
    """당월 [1일, as_of)에 대응하는 전월 구간 [전월 1일, 전월 1일 + 경과 시간)

    전월 말을 넘지 않는다.
    """
    prev_start = _add_months(month_start, -1)
    return prev_start, min(prev_start + (as_of - month_start), month_start)


def measure(logs: Iterable[Mapping[str, Any]], kind: str) -> int:
    """집계값
    - LOG_COUNT: 기록 수, SUBJECT_COUNT: 처리 건수 합, DISTINCT_SUBJECT: 고유 정보주체 수
    - DISTINCT_IP: 고유 접속지 수 — "짧은 시간 여러 IP" (v0.1 보강 K-1)
    - MAX_SUBJECT_REPEAT: 한 회원을 처리한 최대 횟수(그 회원이 든 기록 수) — "특정 정보주체 과도
      조회". 회원번호가 있는 기록만 센다(DB 직접 기록 중 미특정은 빠짐)

    DISTINCT_SUBJECT·MAX_SUBJECT_REPEAT는 1,000명을 넘어 잘린 기록이 있으면 실제보다 작을 수 있다
    (하한값) — 탐지건 요약과 같은 한계
    """
    logs = list(logs)
    if kind == "LOG_COUNT":
        return len(logs)
    if kind == "SUBJECT_COUNT":
        return sum(log["subject_count"] for log in logs)
    if kind == "DISTINCT_IP":
        return len({str(log["client_ip"]) for log in logs})
    if kind == "MAX_SUBJECT_REPEAT":
        top = most_repeated_subject(logs)
        return top[1] if top else 0
    return len({s for log in logs for s in (log["subject_ids"] or [])})


def most_repeated_subject(
    logs: Iterable[Mapping[str, Any]],
) -> tuple[tuple[str, str], int] | None:
    """가장 여러 번 처리된 회원 ((정보주체 유형, 식별값), 횟수) — 같으면 식별값이 작은 쪽"""
    counts: Counter[tuple[str, str]] = Counter()
    for log in logs:
        for subject in set(log["subject_ids"] or []):
            counts[(log["subject_type"], subject)] += 1
    if not counts:
        return None
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0][1]))
