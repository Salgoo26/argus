"""플랫폼 DB 카탈로그 조회 — 결과 컬럼의 이름과 사용자 함수 목록 (게이트웨이 자체 연결)

- 결과 컬럼 설명(RowDescription)은 컬럼을 "테이블 OID + 컬럼 번호"로만 알려 준다
  → 이름으로 바꿔 기록한다. 테이블 구조는 거의 안 바뀌므로 OID별로 캐시한다
- 사용자 스키마의 함수 이름 목록 — `SELECT 사용자함수()`처럼 테이블 없이 개인정보를 처리할 수
  있는 호출을 가려내는 데 쓴다(architecture 3-4 "기록 제외" 예외). 함수는 바뀔 수 있어 60초만 캐시
- 사용자 연결과 섞지 않는다 — 사용자 세션의 트랜잭션·설정에 영향을 주지 않게
"""

import asyncio
import time

import psycopg

FIRST_USER_OID = 16384  # 이보다 작은 OID는 시스템 객체 (PostgreSQL FirstNormalObjectId)
FUNCTIONS_TTL_SEC = 60

_USER_FUNCTIONS_SQL = """
SELECT DISTINCT p.proname
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp%'
"""
_COLUMNS_SQL = """
SELECT c.relname, a.attnum, a.attname
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
 WHERE c.oid = %s AND a.attnum > 0 AND NOT a.attisdropped
"""


class Catalog:
    def __init__(self, conninfo: str) -> None:
        self.conninfo = conninfo
        self._conn: psycopg.AsyncConnection | None = None
        self._lock = asyncio.Lock()
        self._functions: tuple[float, frozenset[str]] = (0.0, frozenset())
        self._tables: dict[int, tuple[str, dict[int, str]]] = {}

    async def _query(self, sql: str, params: tuple | None = None) -> list[tuple]:
        async with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None or self._conn.closed:
                        self._conn = await psycopg.AsyncConnection.connect(
                            self.conninfo, autocommit=True, connect_timeout=5
                        )
                    cur = await self._conn.execute(sql, params)
                    return await cur.fetchall()
                except psycopg.OperationalError:
                    self._conn = None  # DB 재기동 등 — 한 번 다시 연결해 본다
                    if attempt == 2:
                        raise
        return []

    async def user_functions(self) -> frozenset[str]:
        fetched_at, names = self._functions
        if time.monotonic() - fetched_at > FUNCTIONS_TTL_SEC:
            names = frozenset(row[0] for row in await self._query(_USER_FUNCTIONS_SQL))
            self._functions = (time.monotonic(), names)
        return names

    async def column_names(self, columns: list[tuple[int, int]]) -> list[str]:
        """[(테이블 OID, 컬럼 번호)] → ["member.email", …]. 시스템 테이블·계산 컬럼(OID 0)은 뺀다"""
        names = []
        for table_oid, attnum in columns:
            if table_oid < FIRST_USER_OID or attnum <= 0:
                continue
            if table_oid not in self._tables:
                rows = await self._query(_COLUMNS_SQL, (table_oid,))
                if not rows:
                    continue
                self._tables[table_oid] = (rows[0][0], {row[1]: row[2] for row in rows})
            relname, attrs = self._tables[table_oid]
            if attnum in attrs:
                names.append(f"{relname}.{attrs[attnum]}")
        return list(dict.fromkeys(names))
