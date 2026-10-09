"""2티어 정보주체 식별 — 결과에서 회원번호 추출 (v0.1 보강 G-1)

SQL을 해석하지 않는다. DB가 보내는 결과 열 설명(RowDescription)의 "테이블 OID + 열 번호"로
회원을 가리키는 열을 찾고, 결과 행(DataRow)에서 **그 열의 값만** 읽어 회원번호로 기록한다.
결과의 다른 값(이름·이메일 등)은 어디에도 남기지 않는다.
"""

import json
import struct

import pytest
from psycopg import ClientCursor

from app.session import SubjectError, decode_subject

from conftest import connect

NAMES = ("가상일", "가상이", "가상삼", "one@example.com", "two@example.com", "010-0000-0001")


def _statements(store) -> list[dict]:
    return [r["payload"] for r in store.outbox_rows() if r["payload"]["action"] != "LOGIN"]


def _last(store) -> dict:
    return _statements(store)[-1]


def _simple(conn, sql: str):
    with ClientCursor(conn) as cur:
        cur.execute(sql)
        return cur.fetchall() if cur.description else None


def _raw_text(store) -> str:
    return " ".join(r[0] for r in store._conn().execute("SELECT record FROM raw_record"))


@pytest.fixture
def db(gateway, upstream_db):
    with connect(gateway, upstream_db) as conn:
        conn.autocommit = True
        yield conn


def _resolved(event: dict, ids: list[str], count: int | None = None) -> None:
    assert event["subject"]["ids"] == ids
    assert event["subject"]["count"] == (len(ids) if count is None else count)
    assert event["subject"].get("truncated", False) is False
    assert event["context"]["subject_unresolved"] is False


def _unresolved(event: dict, count: int) -> None:
    assert event["subject"]["ids"] == [] and event["subject"]["count"] == count
    assert event["context"]["subject_unresolved"] is True


# ── 단순 질의(텍스트 형식) ────────────────────────────────


def test_member_id_column_is_extracted_and_other_values_are_not_kept(db, store):
    rows = _simple(db, "SELECT id, name, email FROM member ORDER BY id")
    assert len(rows) == 3
    event = _last(store)
    _resolved(event, ["1", "2", "3"])
    assert event["context"]["row_count"] == 3
    # 결과의 다른 값은 원장으로도, 원문 저장소로도 가지 않는다 (원문 저장소엔 SQL·매개변수만)
    everything = json.dumps(_statements(store), ensure_ascii=False) + _raw_text(store)
    assert not any(value in everything for value in NAMES)


def test_alias_and_join_use_the_column_origin(db, store):
    _simple(
        db,
        "SELECT o.member_id AS buyer, o.amount, m.name AS who FROM orders o"
        " JOIN member m ON m.id = o.member_id",
    )
    _resolved(_last(store), ["1", "2"])


def test_repeated_members_are_counted_once(db, store):
    _simple(db, "SELECT o.member_id FROM orders o CROSS JOIN generate_series(1, 3)")
    event = _last(store)
    _resolved(event, ["1", "2"])
    assert event["context"]["row_count"] == 6  # 행 수는 그대로, 정보주체는 고유 회원 수


def test_null_member_references_are_skipped(db, store):
    _simple(
        db,
        "SELECT r.member_id, r.bank_name FROM member m"
        " LEFT JOIN refund_account r ON r.member_id = m.id",
    )
    event = _last(store)
    assert event["data_category"] == "PAYMENT"
    _resolved(event, ["1"])


def test_expression_is_not_a_member_column(db, store):
    # 식·계산 열은 결과 열 설명의 테이블 OID가 0 — 추측하지 않고 미특정(정직하게)
    _simple(db, "SELECT id + 0 AS id, name FROM member")
    _unresolved(_last(store), 3)


def test_query_without_member_column_stays_unresolved(db, store):
    _simple(db, "SELECT name, email FROM member")
    _unresolved(_last(store), 3)


def test_empty_result_with_member_column_is_resolved_to_nobody(db, store):
    _simple(db, "SELECT id FROM member WHERE false")
    event = _last(store)
    _resolved(event, [], 0)


# ── 확장 질의(DBeaver·JDBC 방식) — 텍스트·바이너리 ─────────


def test_extended_query_text_format(db, store):
    db.execute("SELECT id, email FROM member WHERE id = %s", (2,))
    _resolved(_last(store), ["2"])


def test_extended_query_binary_format(db, store):
    with db.cursor(binary=True) as cur:
        cur.execute("SELECT id, name FROM member WHERE id >= %s ORDER BY id", (2,))
        assert [r[0] for r in cur.fetchall()] == [2, 3]
    _resolved(_last(store), ["2", "3"])


def test_reused_prepared_statement_keeps_extracting(db, store):
    # 이름 있는 문장은 두 번째부터 Bind·Execute만 온다 — 열 설명은 연결별로 기억한 것을 쓴다
    for member_id in (1, 3, 3):
        db.execute("SELECT id FROM member WHERE id = %s", (member_id,), prepare=True)
    assert [e["subject"]["ids"] for e in _statements(store)] == [["1"], ["3"], ["3"]]


def test_more_than_1000_members_are_truncated_with_full_count(gateway, upstream_db, store):
    with connect(gateway, upstream_db) as conn:
        with conn.transaction(force_rollback=True):  # 다른 테스트의 데이터에 남기지 않는다
            conn.execute(
                "INSERT INTO member (name, email) SELECT '가상', 'x' || g || '@example.com'"
                " FROM generate_series(1, 1100) g"
            )
            _simple(conn, "SELECT id FROM member")
    event = _last(store)
    assert event["subject"]["count"] == 1103 and len(event["subject"]["ids"]) == 1000
    assert event["subject"]["truncated"] is True
    assert event["context"]["subject_unresolved"] is False


# ── 값 해석 (단위) ────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "fmt", "type_oid", "expected"),
    [
        (b"10293", 0, 20, "10293"),
        (struct.pack("!q", 10293), 1, 20, "10293"),  # int8
        (struct.pack("!i", 42), 1, 23, "42"),  # int4
        (struct.pack("!h", 7), 1, 21, "7"),  # int2
    ],
)
def test_decode_subject(value, fmt, type_oid, expected):
    assert decode_subject(value, fmt, type_oid) == expected


@pytest.mark.parametrize(
    ("value", "fmt", "type_oid"),
    [
        (b"not-a-number", 0, 25),
        (b"12 34", 0, 20),
        (b"\x00\x01", 1, 20),  # 길이가 맞지 않는 바이너리
        (b"abc", 1, 25),  # 해석할 수 없는 바이너리 타입
    ],
)
def test_undecodable_value_raises(value, fmt, type_oid):
    with pytest.raises(SubjectError):
        decode_subject(value, fmt, type_oid)
