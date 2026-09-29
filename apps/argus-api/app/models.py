"""SQLAlchemy 테이블 정의 — 코드에서 쓰는 테이블만, 쓰는 시점에 추가한다.

스키마의 원본은 마이그레이션(=docs/db-schema.md DDL)이다. 여기 정의는 쿼리 작성용이며
CHECK·인덱스 등 제약은 옮기지 않는다 (JPA 엔티티에서 ddl-auto를 끄고 쓰는 것과 같은 구도).
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, CHAR, INET, JSONB

metadata = MetaData()

source_system = Table(
    "source_system",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("code", String(32), nullable=False),
    Column("name", String(100), nullable=False),
)

handler = Table(
    "handler",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("source_system_id", SmallInteger, nullable=False),
    Column("login_id", String(64), nullable=False),
    Column("name", String(50), nullable=False),
    Column("team", String(50)),
    Column("employment_status", String(16), nullable=False),
    Column("terminated_at", DateTime(timezone=True)),
    Column("last_event_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

argus_user = Table(
    "argus_user",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("login_id", String(64), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(16), nullable=False),
    Column("handler_id", BigInteger),
    Column("status", String(16), nullable=False),
    Column("failed_login_count", Integer, nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

access_log = Table(
    "access_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("event_id", Uuid, nullable=False),
    Column("source_system_id", SmallInteger, nullable=False),
    Column("access_path", String(8), nullable=False),
    Column("actor_login_id", String(64), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("client_ip", INET, nullable=False),
    Column("subject_type", String(16)),
    Column("subject_ids", ARRAY(String)),
    Column("subject_count", Integer, nullable=False),
    Column("subject_truncated", Boolean, nullable=False),
    Column("action", String(16), nullable=False),
    Column("data_category", String(32), nullable=False),
    Column("result", String(8), nullable=False),
    Column("request_method", String(8)),
    Column("request_path", String(255)),
    Column("request_query_keys", ARRAY(String)),
    Column("context", JSONB(none_as_null=True)),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("prev_hash", CHAR(64)),
    Column("hash", CHAR(64), nullable=False),
)
