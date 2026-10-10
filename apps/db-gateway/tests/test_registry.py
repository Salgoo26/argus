"""보호 대상 등록부 연동 (v0.1 보강 N-2·N-3)

- 게이트웨이는 Argus 등록부(테이블 → 데이터 유형, 회원 식별 열)로 판정하고 버전을 원장에 남긴다
- **받지 못해도 기록이 NONE으로 빠지지 않는다**: 마지막으로 받은 등록부 → 없으면 고정 표(builtin)
- DB 구조 목록은 이름·자료형만 보낸다 — 카탈로그만 읽고 데이터 값을 읽지 않는다
"""

import json
import re

import pytest
from psycopg import ClientCursor
from psycopg.conninfo import make_conninfo

from app.registry import BUILTIN, Registry, RegistryError, parse
from app.registry_sync import SCHEMA_SQL, RegistrySync, read_schema
from app.sender import HttpResult, sign
from app.sql import TABLE_CATEGORY, analyze

from conftest import connect

SECRET = b"test-registry-secret-0000000000000000"  # 테스트 전용 더미 값
URL = "http://argus-api:8000"


def registry_payload(version: str = "r5", member: str = "PAYMENT", **overrides) -> dict:
    payload = {
        "version": version,
        "database": "platform",
        "tables": {"member": member, "orders": "ORDER"},
        "member_columns": [["member", "id"]],
    }
    return payload | overrides


class FakeArgus:
    """Argus 응답을 흉내 낸다 — getter/poster를 바꿔 끼워 실패·성공을 만든다"""

    def __init__(self) -> None:
        self.registry: HttpResult = HttpResult(None)  # 처음엔 연결 안 됨
        self.posted: list[tuple[str, dict, bytes]] = []
        self.got: list[tuple[str, dict]] = []

    def get(self, url: str, headers: dict) -> HttpResult:
        self.got.append((url, headers))
        return self.registry

    def post(self, url: str, headers: dict, body: bytes) -> HttpResult:
        self.posted.append((url, headers, body))
        return HttpResult(200, b'{"tables":1,"columns":1}')


def make_sync(registry: Registry, argus: FakeArgus, schema: dict | None = None) -> RegistrySync:
    return RegistrySync(
        registry,
        URL,
        SECRET,
        lambda: schema or {"database": "platform", "tables": []},
        getter=argus.get,
        poster=argus.post,
        clock=lambda: 1_760_000_000,
    )


def category(registry: Registry, sql: str) -> str:
    return analyze(sql, frozenset(), registry.current.tables).data_category


# ── 받지 못할 때 (fail-closed) ─────────────────────────────


def test_never_received_uses_the_builtin_table(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    argus = FakeArgus()
    assert make_sync(registry, argus).fetch_registry() is False  # Argus 연결 안 됨
    assert registry.current is BUILTIN and registry.current.version == "builtin"
    # 기록이 NONE으로 빠지지 않는다 — 고정 표로 판정
    assert category(registry, "SELECT * FROM member") == "MEMBER_BASIC"
    assert category(registry, "SELECT * FROM refund_account") == "PAYMENT"


def test_failure_after_a_success_keeps_the_last_registry(tmp_path):
    path = tmp_path / "registry.json"
    registry = Registry(path)
    argus = FakeArgus()
    sync = make_sync(registry, argus)

    argus.registry = HttpResult(200, json.dumps(registry_payload()).encode())
    assert sync.fetch_registry() is True
    assert registry.current.version == "r5"
    assert category(registry, "SELECT * FROM member") == "PAYMENT"

    for failure in (HttpResult(None), HttpResult(503), HttpResult(401), HttpResult(200, b"{")):
        argus.registry = failure
        assert sync.fetch_registry() is False
        assert registry.current.version == "r5"  # 마지막으로 받은 것

    # 재기동해도 마지막 등록부로 시작한다 (gateway-data 볼륨의 파일)
    restarted = Registry(path)
    assert restarted.current.version == "r5"
    assert category(restarted, "SELECT * FROM member") == "PAYMENT"


def test_unreadable_stored_registry_falls_back_to_builtin(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    assert Registry(path).current is BUILTIN


@pytest.mark.parametrize(
    "payload",
    [
        registry_payload(version="v5"),
        registry_payload(database="other"),
        registry_payload(tables={"member": "SECRET"}),
        registry_payload(tables={"member; drop": "MEMBER_BASIC"}),
        registry_payload(member_columns=[["member"]]),
        registry_payload() | {"sample_values": ["가상"]},  # 값 칸이 있으면 거부
    ],
    ids=["version", "database", "category", "table-name", "member-column", "extra-key"],
)
def test_invalid_registry_is_rejected_and_not_applied(tmp_path, payload):
    with pytest.raises(RegistryError):
        parse(payload)
    registry = Registry(tmp_path / "registry.json")
    argus = FakeArgus()
    argus.registry = HttpResult(200, json.dumps(payload).encode())
    assert make_sync(registry, argus).fetch_registry() is False
    assert registry.current is BUILTIN


def test_registry_request_is_signed(tmp_path):
    argus = FakeArgus()
    make_sync(Registry(tmp_path / "r.json"), argus).fetch_registry()
    url, headers = argus.got[0]
    assert url == URL + "/ingest/v1/protection-registry"
    assert headers["X-Argus-Source"] == "PLATFORM"
    assert headers["X-Argus-Signature"] == sign(SECRET, headers["X-Argus-Timestamp"], b"")


def test_builtin_matches_the_old_fixed_tables():
    # 고정 표는 Argus 초기 등록 데이터의 원본이기도 하다 (argus-api 마이그레이션 0021)
    assert dict(BUILTIN.tables) == TABLE_CATEGORY


# ── 원장에 판정 버전 ──────────────────────────────────────


def _last(store) -> dict:
    return [r["payload"] for r in store.outbox_rows() if r["payload"]["action"] != "LOGIN"][-1]


def test_gateway_uses_the_received_registry_and_records_its_version(
    make_gateway, upstream_db, store, tmp_path
):
    registry = Registry(tmp_path / "registry.json")
    registry.apply(registry_payload(version="r7", member="PAYMENT"))
    gw = make_gateway(registry=registry)
    with connect(gw, upstream_db) as conn:
        conn.autocommit = True
        with ClientCursor(conn) as cur:
            cur.execute("SELECT id FROM member ORDER BY id LIMIT 1")
            cur.fetchall()
    event = _last(store)
    assert event["data_category"] == "PAYMENT"  # 등록부가 회원 테이블을 결제수단으로
    assert event["context"]["registry_version"] == "r7"
    assert event["context"]["subject_unresolved"] is False  # 등록부의 회원 식별 열로 추출


def test_without_registry_the_version_is_builtin(gateway, upstream_db, store):
    with connect(gateway, upstream_db) as conn:
        conn.autocommit = True
        with ClientCursor(conn) as cur:
            cur.execute("SELECT name FROM member LIMIT 1")
            cur.fetchall()
    event = _last(store)
    assert event["data_category"] == "MEMBER_BASIC"
    assert event["context"]["registry_version"] == "builtin"


def test_released_member_column_is_no_longer_extracted(make_gateway, upstream_db, store, tmp_path):
    registry = Registry(tmp_path / "registry.json")
    registry.apply(registry_payload(member_columns=[]))  # 회원 식별 열을 모두 해제
    gw = make_gateway(registry=registry)
    with connect(gw, upstream_db) as conn:
        conn.autocommit = True
        with ClientCursor(conn) as cur:
            cur.execute("SELECT id FROM member LIMIT 1")
            cur.fetchall()
    event = _last(store)
    assert event["subject"]["ids"] == [] and event["context"]["subject_unresolved"] is True


# ── DB 구조 목록 — 이름·자료형만 ─────────────────────────


def test_schema_query_reads_only_the_catalog():
    sources = re.findall(r"\b(?:FROM|JOIN)\s+([\w.]+)", SCHEMA_SQL, re.IGNORECASE)
    assert sources and all(s.startswith("pg_catalog.") for s in sources)


def test_schema_has_names_and_types_but_no_values(upstream_db):
    schema = read_schema(make_conninfo(**upstream_db))
    member = next(t for t in schema["tables"] if t["name"] == "member")
    assert member["columns"][:2] == [
        {"name": "id", "type": "bigint"},
        {"name": "name", "type": "text"},
    ]
    text = json.dumps(schema, ensure_ascii=False)
    # 시드 행의 값(이름·이메일)은 실리지 않는다
    assert "@example.com" not in text and "가상" not in text
    assert set(schema) == {"database", "tables"}
    assert all(set(c) == {"name", "type"} for t in schema["tables"] for c in t["columns"])


def test_schema_push_is_signed(tmp_path):
    argus = FakeArgus()
    schema = {"database": "platform", "tables": [{"name": "member", "columns": []}]}
    assert make_sync(Registry(tmp_path / "r.json"), argus, schema).push_schema() is True
    url, headers, body = argus.posted[0]
    assert url == URL + "/ingest/v1/db-schema"
    assert json.loads(body) == schema
    assert headers["X-Argus-Signature"] == sign(SECRET, headers["X-Argus-Timestamp"], body)


def test_schema_read_failure_does_not_send(tmp_path):
    argus = FakeArgus()

    def broken() -> dict:
        raise OSError("db down")

    sync = RegistrySync(Registry(tmp_path / "r.json"), URL, SECRET, broken, argus.get, argus.post)
    assert sync.push_schema() is False and argus.posted == []
