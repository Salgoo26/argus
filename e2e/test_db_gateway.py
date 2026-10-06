"""DB 직접 접속(2티어) E2E — 기능 레이어 8 구현 순서 ① (게이트 B 기준)

관리자 화면에서 DB 접속 토큰 발급 → DB 툴처럼 게이트웨이에 접속(TLS + 플랫폼 아이디 + 토큰)
→ SQL 실행
→ 게이트웨이 버퍼 → Argus 수집 API → 원장에 LOGIN·READ 도착(정규화 SQL·테이블·컬럼·건수·원문 지문).
SQL 리터럴(이메일)은 원장에 없어야 한다 — 원문은 게이트웨이 저장소에만 (절대 규칙 #3).
"""

import os

import psycopg
import pytest
from psycopg import ClientCursor

from conftest import argus_login, platform_login, wait_until

GATEWAY_HOST = os.environ.get("E2E_DB_GATEWAY_HOST", "db-gateway")
PLATFORM_DB = os.environ["E2E_PLATFORM_DB_NAME"]
LITERAL = "e2e-gateway-literal@example.com"


def _connect(login_id: str, token: str, sslmode: str = "require") -> psycopg.Connection:
    return psycopg.connect(
        host=GATEWAY_HOST,
        port=6432,
        dbname=PLATFORM_DB,
        user=login_id,
        password=token,
        sslmode=sslmode,
        application_name="e2e-db-tool",
        connect_timeout=10,
        autocommit=True,
    )


def _ledger(query: str, params: tuple) -> list[tuple]:
    """운영자 확인 — Argus 앱 계정으로 원장 조회 (SELECT만 가능한 계정)"""
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ["ARGUS_DB_HOST"],
        dbname=os.environ["ARGUS_DB_NAME"],
        user=os.environ["ARGUS_DB_USER"],
        password=os.environ["ARGUS_DB_PASSWORD"],
    )
    with psycopg.connect(conninfo) as conn:
        return conn.execute(query, params).fetchall()


def test_db_tool_access_reaches_argus_ledger(officer):
    admin = platform_login("ops_park")
    issued = admin.post("/api/admin/db-tokens")
    assert issued.status_code == 201, issued.text
    token, token_id = issued.json()["token"], issued.json()["token_id"]

    # TLS가 아니면 인증 전에 거부
    with pytest.raises(psycopg.OperationalError, match="TLS required"):
        _connect("ops_park", token, sslmode="disable")

    with _connect("ops_park", token) as conn:
        # DB에는 공용 계정으로 붙는다 — 누가 했는지는 게이트웨이 기록이 정한다
        assert conn.execute("SELECT current_user").fetchone()[0] != "ops_park"
        with ClientCursor(conn) as cur:  # DB 툴 SQL 편집기처럼 리터럴이 SQL에 그대로
            cur.execute(f"SELECT id, name, email FROM member WHERE email = '{LITERAL}'")  # noqa: S608
            assert cur.fetchall() == []
        rows = conn.execute("SELECT id, name FROM member ORDER BY id LIMIT 3").fetchall()
        assert len(rows) == 3

    def arrived():
        found = _ledger(
            "SELECT action, result, data_category, subject_count, context FROM access_log"
            " WHERE access_path = 'DB' AND actor_login_id = 'ops_park'"
            " AND context->>'token_id' = %s ORDER BY id",
            (token_id,),
        )
        return found if len([r for r in found if r[0] == "READ"]) >= 2 else None

    records = wait_until("게이트웨이 기록 원장 도착", arrived)
    assert ("LOGIN", "SUCCESS") in {(r[0], r[1]) for r in records}
    reads = [r for r in records if r[0] == "READ"]
    literal_read, listing = reads[0], reads[1]
    assert literal_read[4]["sql_normalized"].endswith("WHERE email = $1")
    assert listing[2] == "MEMBER_BASIC" and listing[3] == 3
    assert listing[4]["tables"] == ["member"]
    assert listing[4]["columns"] == ["member.id", "member.name"]
    assert listing[4]["row_count"] == 3 and listing[4]["subject_unresolved"] is True
    assert listing[4]["raw_fingerprint"].startswith("sha256:")
    # 리터럴·토큰 값은 원장 어디에도 없다
    assert LITERAL not in str(records) and token not in str(records)

    # 담당자 접속기록 검색에서 접근 경로 "DB"로 보인다
    officer_browser = argus_login(*officer)
    res = officer_browser.post(
        "/api/access-logs/search", json={"actor": "ops_park", "access_path": "DB", "size": 100}
    )
    assert res.status_code == 200, res.text
    assert {"LOGIN", "READ"} <= {item["action"] for item in res.json()["items"]}
