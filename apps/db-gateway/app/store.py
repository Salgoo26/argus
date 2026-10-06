"""원문 저장소 + 전송 버퍼 — gateway-data 볼륨의 SQLite 파일 하나
(architecture 3-4 "원문 저장"·"세부 결정")

왜 플랫폼 DB(outbox)가 아니라 여기인가: 공용 계정이 플랫폼 DB 소유자라, 게이트웨이로 접속한 사람이
전송 전 기록을 지우거나 남의 이름으로 위조할 수 있다. 상용 DB 접근제어도 감사 기록을 감사 대상 DB 밖
자체 저장소에 둔다(§8③). 이 파일은 게이트웨이 컨테이너만 마운트한다.

- raw_record: 원문(SQL·매개변수·접속 정보). Argus는 원문을 갖지 않고 참조(raw_ref)·지문만 받는다
  (절대 규칙 #3 — 매개변수에 이메일·이름이 실릴 수 있음). 고칠 수 없게 트리거로 막는다(append-only)
- outbox: Argus로 보낼 접속기록. 원문과 **같은 트랜잭션**으로 넣는다
  — 원문 없는 기록·기록 없는 원문이 없게
- 지문 = SHA-256(정규화한 원문 JSON). Argus 원장의 지문과 대조해 원문의 위·변조·유실을 확인한다
"""

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_record (
    ref          TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    record       TEXT NOT NULL,
    fingerprint  TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS raw_record_no_update BEFORE UPDATE ON raw_record
BEGIN SELECT RAISE(ABORT, 'raw_record is append-only'); END;
CREATE TRIGGER IF NOT EXISTS raw_record_no_delete BEFORE DELETE ON raw_record
BEGIN SELECT RAISE(ABORT, 'raw_record is append-only'); END;

CREATE TABLE IF NOT EXISTS outbox (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL UNIQUE,
    payload        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'DEAD')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    next_retry_at  REAL NOT NULL,
    last_error     TEXT,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox (status, next_retry_at, id);

-- 한 번만 해야 하는 작업의 표시 (예: 기준선 시드 완료 시각)
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(record: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(record).encode()).hexdigest()


@dataclass(frozen=True)
class PendingEvent:
    id: int
    event_id: str
    payload: dict
    attempts: int


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        # sqlite3 연결은 스레드마다 따로 (게이트웨이 루프 / 전송 스레드)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")  # 커밋 = 디스크 기록 — 정전에도 기록 유실 없음
            self._local.conn = conn
        return conn

    def record(self, raw: dict, event: dict) -> None:
        """원문과 접속기록을 함께 남긴다. 실패하면 예외 — 호출 측은 결과를 내보내지 않는다"""
        self.record_many([(raw, event)])

    def record_many(self, pairs: Sequence[tuple[dict, dict]], mark: str | None = None) -> None:
        """여러 건을 한 트랜잭션으로. mark를 주면 같은 트랜잭션에 완료 표시도 남긴다
        (시드를 다시 실행해도 두 번 들어가지 않게)
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            for raw, event in pairs:
                ref = raw["event_id"]
                digest = fingerprint(raw)
                context = {**event["context"], "raw_ref": ref, "raw_fingerprint": digest}
                conn.execute(
                    "INSERT INTO raw_record (ref, created_at, record, fingerprint)"
                    " VALUES (?, ?, ?, ?)",
                    (ref, raw["occurred_at"], canonical_json(raw), digest),
                )
                conn.execute(
                    "INSERT INTO outbox (event_id, payload, next_retry_at, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (event["event_id"], canonical_json({**event, "context": context}), now, now),
                )
            if mark is not None:
                conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (mark, str(int(now))))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def marked(self, key: str) -> bool:
        return (
            self._conn().execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is not None
        )

    def record_raw(self, raw: dict) -> None:
        """Argus에 보내지 않는 문장(기록 제외 대상)도 원문은 남긴다 (architecture 3-4)"""
        conn = self._conn()
        conn.execute(
            "INSERT INTO raw_record (ref, created_at, record, fingerprint) VALUES (?, ?, ?, ?)",
            (raw["event_id"], raw["occurred_at"], canonical_json(raw), fingerprint(raw)),
        )

    # ── 전송 버퍼 (sender.py) ──

    def due(self, limit: int, now: float) -> list[PendingEvent]:
        rows = self._conn().execute(
            "SELECT id, event_id, payload, attempts FROM outbox"
            " WHERE status = 'PENDING' AND next_retry_at <= ? ORDER BY id LIMIT ?",
            (now, limit),
        )
        return [PendingEvent(r[0], r[1], json.loads(r[2]), r[3]) for r in rows]

    def delete(self, ids: Sequence[int]) -> None:
        self._many("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])

    def mark_dead(self, ids: Sequence[int], error: str) -> None:
        self._many(
            "UPDATE outbox SET status = 'DEAD', attempts = attempts + 1, last_error = ?"
            " WHERE id = ?",
            [(error, i) for i in ids],
        )

    def retry_later(self, events: Sequence[PendingEvent], error: str, delays: Sequence[float]):
        now = time.time()
        self._many(
            "UPDATE outbox SET attempts = attempts + 1, next_retry_at = ?, last_error = ?"
            " WHERE id = ?",
            [(now + d, error, e.id) for e, d in zip(events, delays, strict=True)],
        )

    def _many(self, sql: str, params: list[tuple]) -> None:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(sql, params)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # ── 확인용 (테스트·운영 점검) ──

    def outbox_rows(self) -> list[dict]:
        rows = self._conn().execute(
            "SELECT event_id, payload, status, attempts, last_error FROM outbox ORDER BY id"
        )
        keys = ("event_id", "payload", "status", "attempts", "last_error")
        return [dict(zip(keys, (*r[:1], json.loads(r[1]), *r[2:]), strict=True)) for r in rows]

    def raw(self, ref: str) -> tuple[dict, str] | None:
        row = (
            self._conn()
            .execute("SELECT record, fingerprint FROM raw_record WHERE ref = ?", (ref,))
            .fetchone()
        )
        return None if row is None else (json.loads(row[0]), row[1])
