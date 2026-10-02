"""테스트 공통 fixture.

DATABASE_URL(관리자 권한: CI는 서비스 컨테이너의 ci, 로컬은 argus-db 소유자)로
세션마다 임시 DB를 만들고 → 실제 마이그레이션을 적용하고 → 앱 로그인 계정을 발급한 뒤,
API는 운영과 똑같이 "소유자가 아닌 앱 계정"으로 접속시킨다. 권한 분리까지 실제로 검증하기 위함.
"""

import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.engine import URL, Engine, make_url

from app.config import Settings
from app.ingest.auth import sign
from app.main import create_app
from app.models import detection_rule, detection_rule_history
from app.scripts.provision_db_roles import provision_app_login

APP_ROOT = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get("DATABASE_URL")
TEST_SECRET = "test-ingest-secret-not-for-production"  # 테스트 전용 더미 값
TEST_AUTH_SECRET = "test-auth-secret-not-for-production-0123456789"  # 테스트 전용 더미 값
# 테스트 클라이언트 접속 주소 — 기본값 "testclient"는 IP가 아니라 접속지로 기록할 수 없다
# (RFC 5737 문서용 대역)
TEST_CLIENT_ADDR = ("203.0.113.20", 50000)

requires_db = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL 미설정 — DB 테스트 생략")


@dataclass(frozen=True)
class TestDatabase:
    name: str
    admin_url: URL
    app_login: str
    app_password: str


def create_database(prefix: str) -> tuple[str, URL]:
    name = f"{prefix}_{secrets.token_hex(4)}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(ADMIN_URL).set(drivername="postgresql+psycopg", database=name)
    return name, url


def drop_database(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def alembic_config(url: URL) -> Config:
    cfg = Config(str(APP_ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(scope="session")
def test_db():
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL 미설정 — DB 테스트 생략")
    name, url = create_database("argus_test")
    login = f"argus_api_test_{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    try:
        command.upgrade(alembic_config(url), "head")
        with psycopg.connect(ADMIN_URL, dbname=name, autocommit=True) as conn:
            provision_app_login(conn, login, password)
        yield TestDatabase(name, url, login, password)
    finally:
        drop_database(name)
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


@pytest.fixture(scope="session")
def settings(test_db: TestDatabase, tmp_path_factory) -> Settings:
    return Settings(
        db_host=test_db.admin_url.host,
        db_port=test_db.admin_url.port or 5432,
        db_name=test_db.name,
        db_user=test_db.app_login,
        db_password=test_db.app_password,
        ingest_secret_platform=TEST_SECRET,
        auth_secret=TEST_AUTH_SECRET,
        # TestClient는 http://testserver — Secure 쿠키면 다음 요청에 실리지 않는다
        cookie_secure=False,
        # 소명 첨부 저장소 — 세션마다 임시 디렉터리 (기능 레이어 7 ③)
        attachment_dir=str(tmp_path_factory.mktemp("attachments")),
    )


@pytest.fixture(scope="session")
def admin_engine(test_db: TestDatabase):
    engine = create_engine(test_db.admin_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app(settings: Settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture(scope="session")
def app_engine(app) -> Engine:
    """앱 계정(argus_app 멤버)으로 접속한 엔진 — 운영의 API와 같은 권한"""
    return app.state.engine


@pytest.fixture
def client(app):
    with TestClient(app, client=TEST_CLIENT_ADDR) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_ledger(request):
    """테스트마다 원장을 비운다. TRUNCATE는 소유자만 가능(앱 롤엔 권한 없음)."""
    if "test_db" not in request.fixturenames and "client" not in request.fixturenames:
        yield
        return
    engine = request.getfixturevalue("admin_engine")
    with engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE access_log RESTART IDENTITY CASCADE")
    yield


# ── 탐지 룰 ───────────────────────────────────────────────


@pytest.fixture(scope="session")
def seed_rules(admin_engine) -> list[dict]:
    """마이그레이션이 넣은 기본 룰 — 테스트가 바꾼 룰을 되돌릴 기준"""
    with admin_engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(detection_rule)).mappings()]


def reset_rules(admin_engine, seed_rules: list[dict], enabled: tuple[str, ...]) -> None:
    """룰을 시드 상태로 되돌리고 enabled에 든 룰만 켠다 (탐지건을 먼저 지운 뒤 호출)

    야간·주말 룰은 테스트를 **실행하는 시각**에 따라 결과가 달라진다 — 그 룰을 다루지 않는
    테스트는 꺼 두고, 다루는 테스트는 켜고 고정된 시각의 기록으로 검증한다.
    """
    seed_ids = [r["id"] for r in seed_rules]
    with admin_engine.begin() as conn:
        # 테스트가 만든 룰·남긴 변경 이력부터 지운다 (이력이 룰을 참조) — 소유자 계정
        for seed in seed_rules:
            conn.execute(
                delete(detection_rule_history).where(
                    detection_rule_history.c.rule_id == seed["id"],
                    detection_rule_history.c.version > seed["version"],
                )
            )
        conn.execute(
            delete(detection_rule_history).where(detection_rule_history.c.rule_id.not_in(seed_ids))
        )
        conn.execute(delete(detection_rule).where(detection_rule.c.id.not_in(seed_ids)))
        for seed in seed_rules:
            conn.execute(
                update(detection_rule)
                .where(detection_rule.c.id == seed["id"])
                .values(
                    name=seed["name"],
                    description=seed["description"],
                    severity=seed["severity"],
                    condition=seed["condition"],
                    aggregate=seed["aggregate"],
                    access_path=seed["access_path"],
                    auto_request=seed["auto_request"],
                    version=seed["version"],
                    enabled=seed["name"] in enabled,
                )
            )


# ── 원장 헬퍼 ─────────────────────────────────────────────


def make_entry(**overrides) -> dict:
    """append_access_logs에 넘기는 access_log 컬럼 dict (source_system_id 1 = PLATFORM)"""
    entry = {
        "event_id": uuid.uuid4(),
        "source_system_id": 1,
        "access_path": "APP",
        "actor_login_id": "ops_park",
        "occurred_at": datetime.now(UTC),
        "client_ip": "10.20.3.55",
        "subject_type": "MEMBER",
        "subject_ids": ["10293"],
        "subject_count": 1,
        "subject_truncated": False,
        "action": "READ",
        "data_category": "MEMBER_BASIC",
        "result": "SUCCESS",
        "request_method": "GET",
        "request_path": "/admin/members/10293",
        "request_query_keys": None,
        "context": None,
    }
    entry.update(overrides)
    return entry


# ── 요청 헬퍼 ─────────────────────────────────────────────


def make_event(**overrides) -> dict:
    """api-spec 2-1 예시 형태의 유효한 이벤트 (정보주체는 가상의 내부 PK만)"""
    event = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "actor": {"login_id": "ops_park"},
        "client_ip": "10.20.3.55",
        "action": "DOWNLOAD",
        "subject": {"type": "MEMBER", "ids": ["10293", "10294"], "count": 2},
        "access_path": "APP",
        "data_category": "MEMBER_BASIC",
        "request": {"method": "GET", "path": "/admin/members/export", "query_keys": ["team"]},
        "result": "SUCCESS",
        "context": {},
    }
    event.update(overrides)
    return event


def signed_headers(
    body: bytes,
    *,
    secret: str = TEST_SECRET,
    timestamp: int | None = None,
    source: str = "PLATFORM",
) -> dict:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "Content-Type": "application/json",
        "X-Argus-Source": source,
        "X-Argus-Timestamp": ts,
        "X-Argus-Signature": sign(secret.encode(), ts, body),
    }


def post_events(client: TestClient, events: list, **header_kwargs):
    body = json.dumps({"events": events}).encode()
    return client.post(
        "/ingest/v1/access-logs", content=body, headers=signed_headers(body, **header_kwargs)
    )
