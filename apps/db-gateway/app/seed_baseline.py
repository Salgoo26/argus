"""DB 직접 접근 기준선 시드 — python -m app.seed_baseline (compose의 db-gateway-seed, 일회성)

"DB 직접 전월 대비 급증" 룰(argus-api 마이그레이션 0012)은 지난달 같은 기간 기록이
20건 이상이어야 판정한다. DB 경로는 새로 생긴 경로라 그대로 두면 한 달 동안 울리지 않으므로
시연용 과거 기록을 만든다 (2026-10-06 사용자 결정 — 3티어 기준선 시드
platform-api app/scripts/baseline.py와 같은 원칙).

- Argus 원장에 직접 넣지 않고 **게이트웨이 원문 저장소 + 전송 버퍼**에 넣는다
  → 게이트웨이 전송 루프가 수집 API로 보낸다(서명·검증·해시체인 그대로 — CLAUDE.md 3절 #5).
  원문 레코드도 함께 남겨 Argus 원장의 지문과 대조가 깨지지 않게 하고, 원문에 `seed: true` 표시
- 실제 중계와 같은 함수로 기록을 만든다(sql.analyze → session.statement_event)
  — 형식이 갈라지지 않게
- 모양 (모두 가상, 시드 실행 시점 기준):
  - DB 툴로 회원·주문·문의를 회원번호로 찾아보는 조회만 — 평일 09:00~17:59(한국 시각)
    → 야간·주말 룰에 걸리지 않는다
  - 지난달: 하루 5건 / 이번 달(지금까지): 평소 3건, **mkt_lee만 15건** — 전월 대비 급증 시연용
    (마케팅 담당자의 DB 직접 조회 급증 → 대량 추출 의심). 월초처럼 전월 같은 기간이 20건 미만이면
    룰이 판정하지 않는다(min_baseline)
- 한 번만 넣는다 — 저장소에 완료 표시(meta)를 원문·버퍼와 **같은 트랜잭션**으로 남긴다
- 이 컨테이너엔 DB 비밀번호·서명 키를 주지 않는다 — 원문 저장소 경로와 DB 계정 이름만 필요
"""

import logging
import os
import random
import re
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from app.session import statement_event
from app.sql import analyze
from app.store import Store

logger = logging.getLogger("gateway.seed")

KST = timezone(timedelta(hours=9))
MARK = "baseline_seeded_at"
# (취급자, 접속지) — 3티어 기준선 시드와 같은 사내 PC(같은 사람은 같은 IP)
ACTORS = (("ops_park", "10.20.3.11"), ("mkt_lee", "10.20.3.12"))
SPIKE_ACTOR = "mkt_lee"
PREV_MONTH_PER_DAY = 5
THIS_MONTH_PER_DAY = 3
SPIKE_PER_DAY = 15
WORK_START, WORK_END = time(9, 0), time(18, 0)
FIRST_MEMBER_ID, MEMBER_COUNT = 10001, 500  # 플랫폼 시드 회원 (platform-api app/scripts/seed.py)

# DB 툴에서 흔히 하는 회원번호 기준 조회 — (SQL 원문, 반환 컬럼, 건수 범위)
_QUERIES = (
    (
        "SELECT id, name, email, phone FROM member WHERE id = {member}",
        ["member.id", "member.name", "member.email", "member.phone"],
        (1, 1),
    ),
    (
        "SELECT id, member_id, amount, status FROM orders WHERE member_id = {member}"
        " ORDER BY id DESC LIMIT 20",
        ["orders.id", "orders.member_id", "orders.amount", "orders.status"],
        (0, 5),
    ),
    (
        "SELECT id, member_id, title, status FROM inquiry WHERE member_id = {member}",
        ["inquiry.id", "inquiry.member_id", "inquiry.title", "inquiry.status"],
        (0, 3),
    ),
)
_DB_USER = re.compile(r"^[A-Za-z0-9_]{1,63}$")


def _month_start(moment: datetime) -> datetime:
    return moment.astimezone(KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _weekdays(start: datetime, end: datetime):
    day = start
    while day < end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def baseline_schedule(now: datetime, rng: random.Random) -> list[tuple[datetime, str, str]]:
    """지난달 1일 ~ now 직전까지 (시각, 취급자, 접속지). 시간순"""
    this_month = _month_start(now)
    prev_month = _month_start(this_month - timedelta(days=1))
    timed: list[tuple[datetime, str, str]] = []
    for actor, client_ip in ACTORS:
        this_month_per_day = SPIKE_PER_DAY if actor == SPIKE_ACTOR else THIS_MONTH_PER_DAY
        for start, end, per_day in (
            (prev_month, this_month, PREV_MONTH_PER_DAY),
            (this_month, now, this_month_per_day),
        ):
            for day in _weekdays(start, end):
                for _ in range(per_day):
                    minute = rng.randrange(WORK_START.hour * 60, WORK_END.hour * 60)
                    moment = day + timedelta(minutes=minute, seconds=rng.randrange(60))
                    if moment < now - timedelta(minutes=1):  # 미래·방금 전 기록은 만들지 않는다
                        timed.append((moment, actor, client_ip))
    timed.sort()
    return timed


def baseline_records(now: datetime, db_user: str, rng: random.Random) -> list[tuple[dict, dict]]:
    """(원문, 접속기록) 쌍. 시간순"""
    return [_record(m, actor, ip, db_user, rng) for m, actor, ip in baseline_schedule(now, rng)]


def _record(
    moment: datetime, actor: str, client_ip: str, db_user: str, rng: random.Random
) -> tuple[dict, dict]:
    template, columns, (low, high) = rng.choice(_QUERIES)
    sql = template.format(member=FIRST_MEMBER_ID + rng.randrange(MEMBER_COUNT))
    rows = rng.randint(low, high)
    analysis = analyze(sql)
    event_id = str(uuid.uuid4())
    occurred_at = moment.isoformat(timespec="microseconds")
    raw = {
        "kind": "STATEMENT",
        "seed": True,  # 시연용 가상 기록 — 사고 조사 때 실제 기록과 구분
        "event_id": event_id,
        "occurred_at": occurred_at,
        "login_id": actor,
        "client_ip": client_ip,
        "token_id": None,
        "protocol": "simple",
        "statement_name": "",
        "sql": sql,
        "params": None,
        "param_types": None,
        "result": "SUCCESS",
        "command_tag": f"SELECT {rows}",
        "error_code": None,
        "row_count": rows,
        "sent": True,
        "skip_reason": None,
    }
    event = statement_event(
        event_id=event_id,
        occurred_at=occurred_at,
        login_id=actor,
        client_ip=client_ip,
        token_id=None,
        db_user=db_user,
        analysis=analysis,
        row_count=rows,
        failed=False,
        columns=columns,
    )
    return raw, event


def seed(store: Store, now: datetime, db_user: str, rng: random.Random) -> int:
    """넣은 건수. 이미 넣었으면 0"""
    if store.marked(MARK):
        return 0
    records = baseline_records(now, db_user, rng)
    store.record_many(records, mark=MARK)
    return len(records)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [gateway-seed] %(message)s")
    db_user = os.environ.get("GATEWAY_UPSTREAM_USER", "")
    if not _DB_USER.fullmatch(db_user):
        raise ValueError("GATEWAY_UPSTREAM_USER must match [A-Za-z0-9_]{1,63}")
    store = Store(Path(os.environ.get("GATEWAY_DATA_DIR", "/data")) / "gateway.sqlite3")
    rng = random.Random(20261006)  # noqa: S311 — 가짜 기록의 시각·대상 분산용, 보안 용도 아님
    inserted = seed(store, datetime.now(KST), db_user, rng)
    if inserted:
        logger.info("baseline DB access records queued: %d", inserted)
    else:
        logger.info("baseline already seeded — skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
