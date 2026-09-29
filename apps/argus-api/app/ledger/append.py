"""접속기록 원장의 유일한 INSERT 경로 (CLAUDE.md 3절 #5, db-schema 3-5)

수집 API·Argus 자체 기록·시드 주입이 모두 이 함수를 거친다. DB에 직접 INSERT하면
해시체인이 성립하지 않는다.

직렬화: pg_advisory_xact_lock으로 append 전체를 한 줄로 세운다.
- 체인 머리(직전 hash)를 읽고 → 이어 붙이는 사이에 다른 트랜잭션이 끼어들면 체인이 갈라진다
- id를 락 안에서 발급하므로 id 순서 = 커밋 순서
  → 탐지 배치의 id 커서가 누락 없이 안전 (api-spec 2-6)
- 락은 트랜잭션 종료(커밋·롤백) 시 자동 해제
  — Java의 synchronized 블록을 DB 트랜잭션 범위로 건 것과 비슷
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, func, insert, select

from app.ledger.hashchain import HASHED_FIELDS, compute_hash
from app.models import access_log

# 원장 append 전용 advisory lock 키 (임의의 고정 bigint — 'ARGUS' ASCII)
APPEND_LOCK_KEY = 0x4152475553

# 호출자가 채워야 하는 컬럼 = 해시 대상 중 이 함수가 정하는 것(id·received_at·prev_hash)을 뺀 전부.
# DB default에 기대면 저장값과 해시 계산값이 달라질 수 있어 전부 명시하게 한다.
ENTRY_FIELDS = frozenset(HASHED_FIELDS) - {"id", "received_at", "prev_hash"}


@dataclass(frozen=True)
class AppendResult:
    inserted_ids: list[int]
    duplicate_event_ids: list[uuid.UUID]


def append_access_logs(
    conn: Connection,
    entries: list[dict[str, Any]],
    received_at: datetime | None = None,
) -> AppendResult:
    """entries를 순서대로 원장에 추가한다. event_id가 이미 있으면 건너뛴다(멱등, api-spec 1-3).

    호출자가 연 트랜잭션 안에서 실행되어야 하며, 커밋은 호출자가 한다.
    """
    if not conn.in_transaction():
        raise RuntimeError("append_access_logs must run inside a transaction")
    for entry in entries:
        missing = ENTRY_FIELDS - entry.keys()
        if missing:
            raise ValueError(f"access_log entry missing fields: {sorted(missing)}")

    received_at = received_at or datetime.now(UTC)
    conn.execute(select(func.pg_advisory_xact_lock(APPEND_LOCK_KEY)))

    # 중복 판정도 락 안에서 — 같은 event_id를 동시에 보낸 두 요청이 둘 다 통과하지 않게
    event_ids = [entry["event_id"] for entry in entries]
    seen = set(
        conn.execute(
            select(access_log.c.event_id).where(access_log.c.event_id.in_(event_ids))
        ).scalars()
    )
    prev_hash = conn.execute(
        select(access_log.c.hash).order_by(access_log.c.id.desc()).limit(1)
    ).scalar()

    inserted: list[int] = []
    duplicates: list[uuid.UUID] = []
    for entry in entries:
        if entry["event_id"] in seen:
            duplicates.append(entry["event_id"])
            continue
        seen.add(entry["event_id"])

        new_id = conn.execute(select(func.nextval("access_log_id_seq"))).scalar_one()
        record = {
            **{k: entry[k] for k in ENTRY_FIELDS},
            "id": new_id,
            "received_at": received_at,
            "prev_hash": prev_hash.strip() if prev_hash else None,
        }
        record["hash"] = compute_hash(record)
        conn.execute(insert(access_log).values(**record))
        prev_hash = record["hash"]
        inserted.append(new_id)

    return AppendResult(inserted, duplicates)
