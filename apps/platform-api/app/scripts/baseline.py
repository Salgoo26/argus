"""기준선용 과거 접속기록 — 시드가 outbox에 넣고 relay가 Argus 수집 API로 보낸다 (기능 레이어 4)

"전월 대비 급증" 룰(AGGREGATE)은 지난달 기록이 있어야 판정할 수 있다
(requirements 4-3 "과거 접속기록").
Argus 원장에 직접 넣지 않고 **플랫폼 Agent가 만든 것과 같은 이벤트를 outbox에** 넣는다 — 수집 API의
서명·검증·해시체인 append를 그대로 거친다 (CLAUDE.md 3절 #5 "시드도 수집 API로").

모양 (모두 가상, 시드 실행 시점 기준으로 만든다):
- 회원 목록 조회(READ, 20명씩) 기록만 — 다운로드·야간·주말 기록은 만들지 않는다.
  시드 자체가 다른 룰(대량 다운로드·야간·주말·대량 조회)에 걸려 탐지건이 쏟아지지 않게
- 평일 09:00~17:59(한국 시각)에만, 하루 횟수 고정 — 무작위 횟수면 비율이 우연히 튄다
  - 지난달: 모두 하루 5건
  - 이번 달(지금까지): 평소 3건, **cs_kim만 15건** — 전월 대비 급증 시연용
  - 평소 취급자가 어느 날 시드해도 2배에 닿지 않는지는 tests/test_baseline.py가 1년치 날짜로
    확인한다.
    월초·휴일 직후처럼 전월 같은 기간이 몇 건뿐이면 비율이 튀므로, 룰이 기준선 20건 미만은
    판정하지 않는다(argus-api 마이그레이션 0007 min_baseline) — cs_kim 탐지도 월초엔 안 나온다
- 지지난달 이전 기록은 없다 → 지난달 윈도우는 기준선이 없어 판정하지 않는다
"""

import random
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))
PAGE_SIZE = 20  # 회원 목록 한 화면 (GET /admin/members 기본값)
PREV_MONTH_PER_DAY = 5
THIS_MONTH_PER_DAY = 3
SPIKE_ACTOR = "cs_kim"
SPIKE_PER_DAY = 15
WORK_START, WORK_END = time(9, 0), time(18, 0)


def _month_start(moment: datetime) -> datetime:
    local = moment.astimezone(KST)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _prev_month_start(month_start: datetime) -> datetime:
    return (month_start - timedelta(days=1)).replace(day=1)


def _weekdays(start: datetime, end: datetime):
    day = start
    while day < end:
        if day.weekday() < 5:  # 월~금
            yield day
        day += timedelta(days=1)


def _event(
    actor: str, occurred_at: datetime, client_ip: str, member_ids: list[int]
) -> dict[str, Any]:
    """플랫폼 Agent가 회원 목록 조회에서 만드는 것과 같은 모양 (api-spec 2-1)"""
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": occurred_at.isoformat(timespec="microseconds"),
        "actor": {"login_id": actor},
        "client_ip": client_ip,
        "action": "READ",
        "access_path": "APP",
        "data_category": "MEMBER_BASIC",
        "request": {"method": "GET", "path": "/admin/members", "query_keys": ["page", "size"]},
        "result": "SUCCESS",
        "context": {},
        "subject": {
            "type": "MEMBER",
            "ids": [str(i) for i in member_ids],
            "count": len(member_ids),
            "truncated": False,
        },
    }


def baseline_events(
    actors: list[str], first_member_id: int, member_count: int, now: datetime, rng: random.Random
) -> list[dict[str, Any]]:
    """지난달 1일 ~ now 직전까지의 조회 기록. 시간순"""
    this_month = _month_start(now)
    prev_month = _prev_month_start(this_month)
    pages = max(1, -(-member_count // PAGE_SIZE))
    events: list[tuple[datetime, dict[str, Any]]] = []

    for index, actor in enumerate(actors):
        client_ip = f"10.20.3.{11 + index}"  # 사내 대역(사설 IP) — 취급자별 자리
        periods = (
            (prev_month, this_month, PREV_MONTH_PER_DAY),
            (this_month, now, SPIKE_PER_DAY if actor == SPIKE_ACTOR else THIS_MONTH_PER_DAY),
        )
        for start, end, per_day in periods:
            for day in _weekdays(start, end):
                for _ in range(per_day):
                    minute = rng.randrange(
                        WORK_START.hour * 60, WORK_END.hour * 60
                    )  # 09:00 ~ 17:59
                    occurred_at = day + timedelta(minutes=minute, seconds=rng.randrange(60))
                    if occurred_at >= now - timedelta(minutes=1):
                        continue  # 미래·방금 전 기록은 만들지 않는다
                    page = rng.randrange(pages)
                    first = first_member_id + page * PAGE_SIZE
                    last = min(first + PAGE_SIZE, first_member_id + member_count)
                    ids = list(range(first, last))
                    events.append((occurred_at, _event(actor, occurred_at, client_ip, ids)))

    events.sort(key=lambda pair: pair[0])
    return [event for _, event in events]
