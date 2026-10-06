"""문장(SQL) 기록 — 정규화·수행업무·테이블·컬럼·건수·기록 제외·fail-closed
(architecture 3-4, api-spec 2-2)

psycopg 기본 커서는 확장 질의(Parse·Bind·Execute — DBeaver·JDBC와 같은 방식),
ClientCursor는 단순 질의
(SQL 문자열 하나에 리터럴이 그대로 — DB 툴 SQL 편집기와 같은 모양)로 보낸다.
"""

import json

import psycopg
import pytest
from psycopg import ClientCursor

from app.store import fingerprint

from conftest import connect


def _statements(store) -> list[dict]:
    return [r["payload"] for r in store.outbox_rows() if r["payload"]["action"] != "LOGIN"]


def _raws(store) -> list[dict]:
    rows = store._conn().execute("SELECT record FROM raw_record ORDER BY rowid")

    return [json.loads(r[0]) for r in rows if json.loads(r[0])["kind"] == "STATEMENT"]


def _simple(conn, sql: str):
    with ClientCursor(conn) as cur:
        cur.execute(sql)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def db(gateway, upstream_db):
    with connect(gateway, upstream_db) as conn:
        conn.autocommit = True
        yield conn


# ── 정규화·원문 분리 ─────────────────────────────────────


def test_literal_query_reaches_argus_normalized_only(db, store):
    rows = _simple(db, "SELECT id, name, email FROM member WHERE email = 'one@example.com'")
    assert len(rows) == 1

    [event] = _statements(store)
    assert event["action"] == "READ" and event["result"] == "SUCCESS"
    assert event["data_category"] == "MEMBER_BASIC"
    context = event["context"]
    assert context["sql_normalized"] == "SELECT id, name, email FROM member WHERE email = $1"
    assert context["tables"] == ["member"]
    assert context["columns"] == ["member.id", "member.name", "member.email"]
    assert context["row_count"] == 1
    # 구현 순서 ①: 회원번호 추출 전 — 건수만, 정보주체 미특정
    assert event["subject"] == {"type": "MEMBER", "ids": [], "count": 1}
    assert context["subject_unresolved"] is True
    # 리터럴(이메일)은 Argus로 가지 않는다 — 원문 저장소에만, 지문으로 연결
    assert "one@example.com" not in str(event)
    [raw] = _raws(store)
    assert "one@example.com" in raw["sql"]
    assert fingerprint(raw) == context["raw_fingerprint"]


def test_extended_query_params_stay_in_raw_store(db, store):
    db.execute("UPDATE member SET phone = %s WHERE id = %s", ("010-1234-5678", 2))
    [event] = _statements(store)
    assert event["action"] == "UPDATE" and event["context"]["row_count"] == 1
    assert event["context"]["sql_normalized"] == "UPDATE member SET phone = $1 WHERE id = $2"
    assert "member.phone" in event["context"]["columns"]
    assert "010-1234-5678" not in str(event)
    [raw] = _raws(store)
    assert raw["protocol"] == "extended" and raw["params"] == ["010-1234-5678", "2"]


# ── 데이터 유형 ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "category", "tables"),
    [
        ("SELECT bank_name FROM refund_account", "PAYMENT", ["refund_account"]),
        (
            "SELECT o.member_id, r.bank_name FROM orders o"
            " JOIN refund_account r ON r.member_id = o.member_id",
            "PAYMENT",  # 가장 민감한 것
            ["orders", "refund_account"],
        ),
        ("SELECT amount FROM orders", "ORDER", ["orders"]),
        ("SELECT login_id FROM public.operator", "NONE", ["public.operator"]),
        ("WITH m AS (SELECT * FROM member) SELECT count(*) FROM m", "MEMBER_BASIC", ["member"]),
    ],
)
def test_data_category_and_tables(db, store, sql, category, tables):
    _simple(db, sql)
    [event] = _statements(store)
    assert event["data_category"] == category
    assert event["context"]["tables"] == tables  # CTE 이름은 테이블이 아니다


def test_table_without_personal_data_has_no_subject(db, store):
    _simple(db, "SELECT login_id FROM operator")
    [event] = _statements(store)
    assert event["subject"]["count"] == 0 and "subject_unresolved" not in event["context"]


def test_copy_to_is_download(db, store):
    with db.cursor() as cur, cur.copy("COPY member TO STDOUT") as copy:
        lines = list(copy)
    [event] = _statements(store)
    assert event["action"] == "DOWNLOAD" and event["context"]["row_count"] == len(lines) == 3


# ── 기록 제외 (원문은 남김) ──────────────────────────────


def test_metadata_control_and_ddl_are_not_sent_but_kept_raw(db, store):
    _simple(db, "SELECT version()")  # 테이블 없음
    _simple(db, "SELECT relname FROM pg_catalog.pg_class LIMIT 1")  # 카탈로그만
    _simple(db, "SET application_name = 'dbeaver'")
    with db.transaction():  # BEGIN / COMMIT
        pass
    _simple(db, "CREATE TEMP TABLE scratch (id int)")  # DDL — v0.1은 원문 저장소에만
    assert _statements(store) == []
    reasons = [r["skip_reason"] for r in _raws(store)]
    assert {"NO_USER_TABLE", "CONTROL", "DDL_DCL"} <= set(reasons)
    assert all(r["sent"] is False for r in _raws(store))


@pytest.mark.parametrize(
    ("sql", "action"),
    [
        ("SELECT member_email(1)", "READ"),  # 테이블 이름 없이 회원 이메일을 읽는다
        ("CALL touch_member(1)", "UPDATE"),
        ("DO 'BEGIN PERFORM 1; END'", "UPDATE"),
    ],
)
def test_user_function_do_and_call_are_sent(db, store, sql, action):
    _simple(db, sql)
    [event] = _statements(store)
    # 안을 볼 수 없으므로 개인정보 처리로 가정 (2026-10-06 사용자 결정)
    assert event["action"] == action and event["data_category"] == "MEMBER_BASIC"
    assert event["context"]["subject_unresolved"] is True


# ── 실패·여러 문장·문장 재사용 ───────────────────────────


def test_failed_statement_is_recorded_as_failure(db, store):
    with pytest.raises(psycopg.errors.UndefinedColumn):
        _simple(db, "SELECT nope FROM member")
    [event] = _statements(store)
    assert event["result"] == "FAILURE" and event["context"]["row_count"] == 0
    assert event["subject"]["count"] == 0
    assert [r["error_code"] for r in _raws(store)] == ["42703"]


def test_each_statement_in_one_query_string_is_recorded(db, store):
    _simple(db, "SELECT id FROM member WHERE id = 1; DELETE FROM orders WHERE id = 999")
    events = _statements(store)
    assert [(e["action"], e["context"]["row_count"]) for e in events] == [
        ("READ", 1),
        ("DELETE", 0),
    ]


def test_statements_after_an_error_did_not_run_and_are_not_recorded(db, store):
    with pytest.raises(psycopg.errors.UndefinedTable):
        _simple(db, "SELECT 1 FROM nowhere; DELETE FROM orders")
    assert [e["result"] for e in _statements(store)] == ["FAILURE"]
    assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 2  # DELETE는 실행되지 않음


def test_reused_prepared_statement_keeps_sql_and_columns(gateway, upstream_db, store):
    # pgjdbc처럼 이름 있는 문장을 만든 뒤 Bind·Execute만 다시 보내는 경우 (2026-10-06 실측)
    with connect(gateway, upstream_db) as conn:
        conn.prepare_threshold = 0
        for member_id in (1, 2, 3):
            conn.execute("SELECT id, email FROM member WHERE id = %s", (member_id,), prepare=True)
    events = _statements(store)
    assert len(events) == 3
    assert {e["context"]["sql_normalized"] for e in events} == {
        "SELECT id, email FROM member WHERE id = $1"
    }
    assert all(e["context"]["columns"] == ["member.id", "member.email"] for e in events)
    assert [r["params"] for r in _raws(store) if r["sent"]] == [["1"], ["2"], ["3"]]


# ── fail-closed ──────────────────────────────────────────


def test_completion_becomes_error_when_statement_cannot_be_recorded(
    make_gateway, upstream_db, store
):
    original = store.record

    def statements_fail(raw, event):
        if raw["kind"] == "STATEMENT":
            raise OSError("disk full")
        return original(raw, event)

    store.record = statements_fail
    gw = make_gateway(store=store)
    with connect(gw, upstream_db) as conn:
        with pytest.raises(psycopg.OperationalError, match="access log unavailable"):
            conn.execute("SELECT email FROM member").fetchall()
        assert conn.closed or conn.broken
