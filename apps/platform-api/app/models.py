"""SQLAlchemy 테이블 정의 — 코드에서 쓰는 테이블만, 쓰는 시점에 추가한다.

스키마의 원본은 마이그레이션(=docs/db-schema.md 4절 DDL)이다. 여기 정의는 쿼리 작성용이며
CHECK·인덱스 등 제약은 옮기지 않는다 (argus-api와 같은 방식).
"""

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

operator = Table(
    "operator",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("login_id", String(64), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("name", String(50), nullable=False),
    Column("team", String(50), nullable=False),
    Column("role", String(16), nullable=False),
    Column("employment_status", String(16), nullable=False),
    Column("terminated_at", DateTime(timezone=True)),
    Column("failed_login_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

member = Table(
    "member",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("email", String(255), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("name", String(50), nullable=False),
    Column("phone", String(20)),
    Column("address", String(255)),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("withdrawn_at", DateTime(timezone=True)),
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("event_id", Uuid, nullable=False),
    Column("topic", String(16), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_retry_at", DateTime(timezone=True), nullable=False),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
