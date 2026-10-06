"""테스트 공통 fixture.

DATABASE_URL(관리자 권한: CI는 서비스 컨테이너의 ci, 로컬은 platform-db 소유자)로
세션마다 임시 DB를 만들고
게이트웨이가 조회하는 operator 테이블만 최소로 만든다(플랫폼 마이그레이션과 독립).
게이트웨이는 이 DB를
"플랫폼 DB"로, DATABASE_URL의 계정을 "공용 계정"으로 삼아 실제 SCRAM 로그인·중계를 한다.
"""

import asyncio
import os
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.auth import fetch_operator_status
from app.catalog import Catalog
from app.server import Gateway, UpstreamConfig
from app.store import Store
from app.tls import ensure_self_signed, server_context

ADMIN_URL = os.environ.get("DATABASE_URL")
TEST_TOKEN_KEY = b"test-db-gateway-key-not-for-production-01"  # 테스트 전용 더미 값

OPERATOR_DDL = """
CREATE TABLE operator (
    id                  bigserial   PRIMARY KEY,
    login_id            varchar(64) NOT NULL UNIQUE,
    employment_status   varchar(16) NOT NULL DEFAULT 'ACTIVE',
    failed_login_count  int         NOT NULL DEFAULT 0
);
INSERT INTO operator (login_id) VALUES ('ops_park'), ('cs_kim');
INSERT INTO operator (login_id, employment_status) VALUES ('retired_lee', 'TERMINATED');
INSERT INTO operator (login_id, failed_login_count) VALUES ('locked_choi', 5);

-- 플랫폼 업무 테이블 축약판 (가상 데이터) — 데이터 유형 매핑 확인용
CREATE TABLE member (id bigserial PRIMARY KEY, name text, email text, phone text);
CREATE TABLE orders (id bigserial PRIMARY KEY, member_id bigint REFERENCES member(id), amount int);
CREATE TABLE refund_account (id bigserial PRIMARY KEY, member_id bigint, bank_name text);
INSERT INTO member (name, email, phone) VALUES
    ('가상일', 'one@example.com', '010-0000-0001'),
    ('가상이', 'two@example.com', '010-0000-0002'),
    ('가상삼', 'three@example.com', '010-0000-0003');
INSERT INTO orders (member_id, amount) VALUES (1, 1000), (2, 2000);
INSERT INTO refund_account (member_id, bank_name) VALUES (1, '가상은행');

-- 테이블 이름 없이 개인정보를 처리하는 사용자 함수·프로시저 (architecture 3-4 "기록 제외" 예외)
CREATE FUNCTION member_email(member_id bigint) RETURNS text LANGUAGE sql
    AS 'SELECT email FROM member WHERE id = member_id';
CREATE PROCEDURE touch_member(member_id bigint) LANGUAGE sql
    AS 'UPDATE member SET phone = phone WHERE id = member_id';
"""


@pytest.fixture(scope="session")
def upstream_db():
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL 미설정 — DB 테스트 생략")
    name = f"gateway_test_{secrets.token_hex(4)}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    info = conninfo_to_dict(ADMIN_URL)
    info["dbname"] = name
    with psycopg.connect(make_conninfo(**info), autocommit=True) as conn:
        conn.execute(OPERATOR_DDL)
    try:
        yield info
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


class RunningGateway:
    """게이트웨이를 별도 스레드의 이벤트 루프에서 띄운다
    — 테스트는 일반 DB 클라이언트(psycopg)로 접속"""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            self.server = self.loop.run_until_complete(
                asyncio.start_server(gateway.handle, "127.0.0.1", 0)
            )
            self.port = self.server.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(10)

    def stop(self) -> None:
        async def shutdown() -> None:
            self.server.close()
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "gateway.sqlite3")


@pytest.fixture
def make_gateway(upstream_db, store, tmp_path):
    cert, key = ensure_self_signed(tmp_path / "tls")
    conninfo = make_conninfo(**upstream_db)
    running = []

    def start(**overrides) -> RunningGateway:
        options = {
            "store": store,
            "ssl_context": server_context(cert, key),
            "token_key": TEST_TOKEN_KEY,
            "upstream_config": UpstreamConfig(
                upstream_db.get("host", "localhost"),
                int(upstream_db.get("port", 5432)),
                upstream_db["dbname"],
                upstream_db["user"],
                upstream_db["password"],
            ),
            "operator_lookup": lambda login_id: fetch_operator_status(conninfo, login_id),
            "catalog": Catalog(conninfo),
        }
        options.update(overrides)
        gw = RunningGateway(Gateway(**options))
        running.append(gw)
        return gw

    yield start
    for gw in running:
        gw.stop()


@pytest.fixture
def gateway(make_gateway) -> RunningGateway:
    return make_gateway()


def make_token(
    login_id: str = "ops_park",
    *,
    key: bytes = TEST_TOKEN_KEY,
    audience: str = "db-gateway",
    lifetime: timedelta = timedelta(hours=1),
    token_id: str | None = None,
) -> tuple[str, str]:
    """platform-api의 발급(app/dbtoken/router.py)과 같은 형태의 토큰"""
    now = datetime.now(UTC).replace(microsecond=0)
    token_id = token_id or str(uuid.uuid4())
    claims = {"sub": login_id, "jti": token_id, "aud": audience, "iat": now, "exp": now + lifetime}
    return jwt.encode(claims, key, algorithm="HS256"), token_id


def connect(gw: RunningGateway, db: dict, login_id: str = "ops_park", password: str | None = None,
            **params) -> psycopg.Connection:  # fmt: skip
    if password is None:
        password, _ = make_token(login_id)
    return psycopg.connect(
        host="127.0.0.1",
        port=gw.port,
        dbname=params.pop("dbname", db["dbname"]),
        user=login_id,
        password=password,
        sslmode=params.pop("sslmode", "require"),
        connect_timeout=10,
        **params,
    )
