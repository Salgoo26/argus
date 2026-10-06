"""게이트웨이 중계·인증·LOGIN 기록 (architecture 3-4, api-spec 2-2·2-4 "DB 직접 접근")

실제 PostgreSQL을 "플랫폼 DB"로 두고,
일반 DB 클라이언트(psycopg = libpq, DBeaver의 pgjdbc와 같은 규약)로
게이트웨이에 접속한다.
"""

import time
from datetime import timedelta

import psycopg
import pytest

from app.store import fingerprint

from conftest import connect, make_token


def _logins(store) -> list[dict]:
    return [row["payload"] for row in store.outbox_rows()]


def _raw_for(store, event: dict) -> dict:
    record, digest = store.raw(event["context"]["raw_ref"])
    assert digest == event["context"]["raw_fingerprint"] == fingerprint(record)
    return record


# ── 성공 ─────────────────────────────────────────────────


def test_token_login_relays_to_platform_db_as_shared_account(gateway, upstream_db, store):
    token, token_id = make_token("ops_park")
    with connect(gateway, upstream_db, password=token, application_name="DBeaver test") as conn:
        # DB에는 공용 계정으로 붙는다 — 사람별 DB 계정이 없다
        assert conn.execute("SELECT current_user").fetchone()[0] == upstream_db["user"]
        assert conn.execute("SELECT count(*) FROM operator").fetchone()[0] == 4
        # 확장 쿼리 프로토콜(매개변수)도 그대로 중계된다
        assert conn.execute("SELECT %s::int + 1", (41,)).fetchone()[0] == 42

    [event] = _logins(store)
    assert event["action"] == "LOGIN" and event["result"] == "SUCCESS"
    assert event["access_path"] == "DB" and event["data_category"] == "NONE"
    assert event["actor"] == {"login_id": "ops_park"}  # 실사용자 = 토큰 주인 = 플랫폼 아이디
    assert event["client_ip"] == "127.0.0.1"
    assert event["context"]["db_user"] == upstream_db["user"]
    assert event["context"]["token_id"] == token_id
    assert "subject" not in event and "request" not in event

    raw = _raw_for(store, event)
    assert raw["kind"] == "LOGIN" and raw["result"] == "SUCCESS"
    assert raw["params"] == {"database": upstream_db["dbname"], "application_name": "DBeaver test"}
    assert raw["tls"]["version"].startswith("TLS")
    # 토큰 값(비밀번호)은 원문에도 기록에도 남지 않는다
    assert token not in str(raw) and token not in str(event)


def test_event_fields_match_argus_ingest_rules(gateway, upstream_db, store):
    # Argus 수집 검증(api-spec 2-2 v0.5)이 요구하는 DB 기록 형식
    # — 형식이 어긋나면 Argus가 DEAD로 거부한다
    connect(gateway, upstream_db).close()
    [event] = _logins(store)
    context = event["context"]
    assert set(context) == {"db_user", "token_id", "raw_ref", "raw_fingerprint"}
    assert (
        context["raw_fingerprint"].startswith("sha256:") and len(context["raw_fingerprint"]) == 71
    )
    assert context["raw_ref"] == event["event_id"]


# ── TLS ──────────────────────────────────────────────────


def test_non_tls_connection_is_refused_before_auth(gateway, upstream_db, store):
    with pytest.raises(psycopg.OperationalError, match="TLS required"):
        connect(gateway, upstream_db, sslmode="disable")
    assert _logins(store) == []  # 누구인지 확인하기 전 — 기록 대상 아님


# ── 인증 실패 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("login_id", "token_owner", "options", "reason", "message"),
    [
        ("ops_park", "ops_park", {"key": b"x" * 40}, "INVALID_TOKEN", "invalid DB access token"),
        # 관리자 로그인 쿠키처럼 용도가 다른 토큰
        ("ops_park", "ops_park", {"audience": "platform-admin"}, "WRONG_AUDIENCE", "invalid"),
        # 남의 토큰을 자기 아이디로
        ("ops_park", "cs_kim", {}, "TOKEN_OWNER_MISMATCH", "invalid DB access token"),
        ("ops_park", "ops_park", {"lifetime": timedelta(seconds=-1)}, "TOKEN_EXPIRED", "expired"),
        ("retired_lee", "retired_lee", {}, "ACCOUNT_DISABLED", "disabled"),
        ("locked_choi", "locked_choi", {}, "ACCOUNT_LOCKED", "locked"),
    ],
)
def test_failed_login_is_recorded(
    gateway, upstream_db, store, login_id, token_owner, options, reason, message
):
    token, token_id = make_token(token_owner, **options)
    with pytest.raises(psycopg.OperationalError, match=message):
        connect(gateway, upstream_db, login_id, password=token)

    [event] = _logins(store)
    assert event["action"] == "LOGIN" and event["result"] == "FAILURE"
    assert event["actor"] == {"login_id": login_id}
    raw = _raw_for(store, event)
    assert raw["failure_reason"] == reason
    # 서명이 유효한 토큰만 토큰 ID를 남긴다 — 위조 토큰의 jti는 믿을 수 없다
    signed_ok = reason != "INVALID_TOKEN"
    assert (event["context"].get("token_id") == token_id) is signed_ok


def test_unknown_login_id_leaves_no_trace(gateway, upstream_db, store):
    # 아이디 칸에 잘못 입력된 값(비밀번호 등)이 영구 저장되지 않게 — 원문 저장소에도 남기지 않는다
    with pytest.raises(psycopg.OperationalError, match="invalid DB access token"):
        connect(gateway, upstream_db, "my-password-typed-here", password="whatever")
    assert _logins(store) == []
    assert store._conn().execute("SELECT count(*) FROM raw_record").fetchone()[0] == 0


def test_other_database_is_refused(gateway, upstream_db, store):
    with pytest.raises(psycopg.OperationalError, match="only the platform database"):
        connect(gateway, upstream_db, dbname="postgres")
    assert _raw_for(store, _logins(store)[0])["failure_reason"] == "DATABASE_NOT_ALLOWED"


# ── fail-closed ──────────────────────────────────────────


def test_login_refused_when_access_log_cannot_be_written(make_gateway, upstream_db, store):
    def broken(*_):
        raise OSError("disk full")

    store.record = broken
    gw = make_gateway(store=store)
    with pytest.raises(psycopg.OperationalError, match="access log unavailable"):
        connect(gw, upstream_db)


def test_login_refused_when_account_status_unknown(make_gateway, upstream_db, store):
    async def unreachable(_login_id):
        raise OSError("platform db down")

    gw = make_gateway(operator_lookup=unreachable)
    with pytest.raises(psycopg.OperationalError, match="temporarily unavailable"):
        connect(gw, upstream_db)
    assert _logins(store) == []


# ── 토큰 만료 시 연결 종료 ───────────────────────────────


def test_connection_is_closed_at_token_expiry(gateway, upstream_db):
    token, _ = make_token(lifetime=timedelta(seconds=3))
    with connect(gateway, upstream_db, password=token) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        time.sleep(4)
        with pytest.raises(psycopg.OperationalError):
            conn.execute("SELECT 1")
