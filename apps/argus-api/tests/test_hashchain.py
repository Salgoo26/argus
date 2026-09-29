"""해시체인 — 연속성, 변조·삭제 탐지, 동시 append 직렬화, 멱등"""

import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.ledger.append import append_access_logs
from app.ledger.hashchain import compute_hash, verify_chain
from app.models import access_log

from conftest import make_entry


def _append(engine, n: int, **overrides) -> list[int]:
    with engine.begin() as conn:
        return append_access_logs(conn, [make_entry(**overrides) for _ in range(n)]).inserted_ids


def test_chain_links_and_verifies(app_engine):
    _append(app_engine, 3)
    _append(app_engine, 2)

    with app_engine.connect() as conn:
        rows = conn.execute(select(access_log).order_by(access_log.c.id)).mappings().all()
        result = verify_chain(conn)

    assert result.ok and result.checked == 5
    assert rows[0]["prev_hash"] is None  # 체인 시작점
    for prev, cur in zip(rows, rows[1:], strict=False):
        assert cur["prev_hash"] == prev["hash"]


def test_hash_survives_db_roundtrip_for_tricky_values(app_engine):
    # 정규화 규칙이 DB 저장·조회 뒤에도 같은 해시를 내는지 — IPv6, 한글, 중첩 JSON, 비UTC 시각
    kst = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=123456)
    _append(
        app_engine,
        1,
        client_ip="2001:DB8:0:0::1",
        occurred_at=kst - timedelta(days=30),
        subject_ids=[],
        subject_count=0,
        context={"reason": "민원 대응 확인", "target": {"detection_id": 7}},
    )
    with app_engine.connect() as conn:
        row = dict(conn.execute(select(access_log)).mappings().one())
    assert compute_hash(row) == row["hash"].strip()
    assert str(row["client_ip"]) == "2001:db8::1"


def test_tampering_is_detected(admin_engine, app_engine):
    ids = _append(app_engine, 3)
    # 슈퍼유저가 트리거까지 끄고 가운데 레코드를 고친 상황
    with admin_engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE access_log DISABLE TRIGGER trg_access_log_no_update")
        conn.execute(
            text("UPDATE access_log SET subject_count = 999 WHERE id = :id"), {"id": ids[1]}
        )
        conn.exec_driver_sql("ALTER TABLE access_log ENABLE TRIGGER trg_access_log_no_update")

    with app_engine.connect() as conn:
        result = verify_chain(conn)
    assert not result.ok
    assert result.broken_at_id == ids[1]
    assert result.reason == "hash mismatch (tampered)"


def test_deletion_is_detected(admin_engine, app_engine):
    ids = _append(app_engine, 3)
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM access_log WHERE id = :id"), {"id": ids[1]})

    with app_engine.connect() as conn:
        result = verify_chain(conn)
    assert not result.ok
    assert result.broken_at_id == ids[2]
    assert result.reason == "prev_hash does not link"


def test_concurrent_appends_keep_single_chain(app_engine):
    # advisory lock이 없으면 두 트랜잭션이 같은 직전 hash를 읽어 체인이 갈라진다
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(5):
                _append(app_engine, 4)
        except Exception as exc:  # pragma: no cover — 실패 시 원인 확인용
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    with app_engine.connect() as conn:
        result = verify_chain(conn)
    assert result.ok and result.checked == 6 * 5 * 4


def test_duplicate_event_ids_are_skipped(app_engine):
    dup = uuid.uuid4()
    with app_engine.begin() as conn:
        first = append_access_logs(conn, [make_entry(event_id=dup), make_entry(event_id=dup)])
    with app_engine.begin() as conn:
        again = append_access_logs(conn, [make_entry(event_id=dup), make_entry()])

    assert len(first.inserted_ids) == 1 and first.duplicate_event_ids == [dup]
    assert len(again.inserted_ids) == 1 and again.duplicate_event_ids == [dup]
    with app_engine.connect() as conn:
        assert verify_chain(conn).checked == 2
