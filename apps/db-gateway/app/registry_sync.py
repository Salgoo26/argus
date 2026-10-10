"""보호 대상 등록부 동기화 (v0.1 보강 N-2·N-3) — 게이트웨이 ↔ Argus, 전송 루프와 같은 HMAC 서명

- 등록부 받기: 기동 시 + 5분마다 GET /ingest/v1/protection-registry → Registry.apply
  (실패하면 지금 쓰는 표를 그대로 — 마지막으로 받은 것 또는 고정 표, app/registry.py)
- DB 구조 목록 보내기: 기동 시 + 1시간마다 POST /ingest/v1/db-schema
  **테이블·컬럼 이름과 자료형만** — 카탈로그(pg_catalog)만 읽고 데이터 값을 읽는 쿼리는 쓰지 않는다.
  등록 화면이 이 목록에서 고르고(오타 방지), 등록부에 없는 컬럼은 "미분류"로 드러난다
- 게이트웨이 자체 연결로 조회하고, 사용자 행위가 아니라 접속기록을 남기지 않는다
  (카탈로그 조회와 같음)
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

import psycopg

from app.registry import DB, Registry, RegistryError
from app.sender import HTTP_TIMEOUT_SEC, HttpResult, Poster, sign, urllib_post

logger = logging.getLogger("gateway.registry")

REGISTRY_PATH = "/ingest/v1/protection-registry"
SCHEMA_PATH = "/ingest/v1/db-schema"
REGISTRY_INTERVAL_SEC = 300  # 【기본값】 5분
SCHEMA_INTERVAL_SEC = 3600  # 【기본값】 1시간
RETRY_SEC = 30  # 실패하면(Argus 기동 전 등) 30초 뒤 다시
SOURCE = "PLATFORM"

# 이름과 자료형만 — 시스템 카탈로그 외의 테이블을 읽지 않는다 (tests/test_registry.py가 확인)
SCHEMA_SQL = """
SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
   AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY c.relname, a.attnum
"""

Getter = Callable[[str, dict[str, str]], HttpResult]


def urllib_get(url: str, headers: dict[str, str]) -> HttpResult:
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 — 스킴은 설정 단계에서 http(s)로 제한
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:  # noqa: S310
            return HttpResult(response.status, response.read())
    except urllib.error.HTTPError as error:
        return HttpResult(error.code, error.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return HttpResult(None)


def read_schema(conninfo: str) -> dict:
    """플랫폼 DB 구조 — {"database", "tables": [{"name", "columns": [{"name", "type"}]}]}"""
    with psycopg.connect(conninfo, autocommit=True, connect_timeout=5) as conn:
        rows = conn.execute(SCHEMA_SQL).fetchall()
    tables: dict[str, list[dict]] = {}
    for table, column, type_ in rows:
        tables.setdefault(table, []).append({"name": column, "type": type_})
    return {
        "database": DB,
        "tables": [{"name": t, "columns": cols} for t, cols in tables.items()],
    }


class RegistrySync:
    def __init__(
        self,
        registry: Registry,
        base_url: str,
        secret: bytes,
        schema_reader: Callable[[], dict],
        getter: Getter = urllib_get,
        poster: Poster = urllib_post,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.registry = registry
        self.base_url = base_url
        self.secret = secret
        self.schema_reader = schema_reader
        self.getter = getter
        self.poster = poster
        self.clock = clock

    def _headers(self, body: bytes) -> dict[str, str]:
        timestamp = str(int(self.clock()))
        return {
            "X-Argus-Source": SOURCE,
            "X-Argus-Timestamp": timestamp,
            "X-Argus-Signature": sign(self.secret, timestamp, body),
        }

    def fetch_registry(self) -> bool:
        """받아서 바꿔 끼웠으면 True. 실패하면 지금 표를 그대로 쓴다(로그만)"""
        result = self.getter(self.base_url + REGISTRY_PATH, self._headers(b""))
        if result.status != 200:
            logger.warning(
                "registry not received (%s) — keeping %s",
                result.status or "NETWORK_ERROR",
                self.registry.current.version,
            )
            return False
        try:
            return self.registry.apply(json.loads(result.body))
        except (ValueError, RegistryError):
            logger.error("registry response rejected — keeping %s", self.registry.current.version)
            return False

    def push_schema(self) -> bool:
        try:
            schema = self.schema_reader()
        except Exception:
            logger.exception("DB structure could not be read")
            return False
        body = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()
        headers = self._headers(body) | {"Content-Type": "application/json; charset=utf-8"}
        result = self.poster(self.base_url + SCHEMA_PATH, headers, body)
        if result.status != 200:
            logger.warning("DB structure not delivered (%s)", result.status or "NETWORK_ERROR")
            return False
        logger.info("DB structure sent: %d tables", len(schema["tables"]))
        return True


def run_registry_sync(sync: RegistrySync, stop: threading.Event) -> None:
    """기동 시 둘 다 한 번 — 이후 등록부는 5분, 구조 목록은 1시간마다"""
    next_schema = next_registry = 0.0
    while not stop.is_set():
        now = time.monotonic()
        try:
            if now >= next_schema:
                ok = sync.push_schema()
                next_schema = now + (SCHEMA_INTERVAL_SEC if ok else RETRY_SEC)
            if now >= next_registry:
                # 받지 못하면 지금 표(마지막 등록부 또는 고정 표)를 쓰면서 30초 뒤 다시
                received = sync.fetch_registry() or sync.registry.current.version != "builtin"
                next_registry = now + (REGISTRY_INTERVAL_SEC if received else RETRY_SEC)
        except Exception:
            logger.exception("registry sync cycle failed")
        stop.wait(max(1.0, min(next_schema, next_registry) - time.monotonic()))
