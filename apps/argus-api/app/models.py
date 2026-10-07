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
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
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

detection_rule = Table(
    "detection_rule",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("description", Text),
    Column("rule_type", String(16), nullable=False),
    Column("access_path", String(8), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("auto_request", Boolean, nullable=False),
    Column("condition", JSONB, nullable=False),
    # None은 SQL NULL로 — JSON null이면 CHECK(EVENT ↔ aggregate IS NULL)에 걸린다
    Column("aggregate", JSONB(none_as_null=True)),
    Column("group_by", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_by", BigInteger),  # NULL = 시스템(마이그레이션 시드)
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# 룰 변경 이력 (LOG-13) — 추가·조회만 (0008)
detection_rule_history = Table(
    "detection_rule_history",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("rule_id", BigInteger, nullable=False),
    Column("version", Integer, nullable=False),
    Column("change_type", String(16), nullable=False),
    Column("snapshot", JSONB, nullable=False),
    Column("changed_by", BigInteger),  # NULL = 시스템(마이그레이션)
    Column("changed_at", DateTime(timezone=True), nullable=False),
)

detection = Table(
    "detection",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("rule_id", BigInteger, nullable=False),
    Column("rule_version", Integer, nullable=False),
    Column("rule_snapshot", JSONB, nullable=False),
    Column("source_system_id", SmallInteger, nullable=False),
    Column("access_path", String(8), nullable=False),  # 탐지건 하나 = 경로 하나 (policy 1-5)
    Column("actor_login_id", String(64), nullable=False),
    Column("group_bucket", String(64), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("status", String(16), nullable=False),
    Column("round", Integer, nullable=False),
    Column("log_count", Integer, nullable=False),
    Column("aggregate_value", Numeric),
    Column("log_summary", JSONB),
    Column("first_occurred_at", DateTime(timezone=True), nullable=False),
    Column("last_occurred_at", DateTime(timezone=True), nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True)),
    Column("close_reason", Text),
)

detection_log = Table(
    "detection_log",
    metadata,
    Column("detection_id", BigInteger, primary_key=True),
    Column("access_log_id", BigInteger, primary_key=True),
)

detection_status_history = Table(
    "detection_status_history",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("detection_id", BigInteger, nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16), nullable=False),
    Column("round", Integer, nullable=False),
    Column("actor_user_id", BigInteger),
    Column("comment", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

detection_batch_run = Table(
    "detection_batch_run",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("from_access_log_id", BigInteger, nullable=False),
    Column("to_access_log_id", BigInteger, nullable=False),
    Column("processed_count", Integer, nullable=False),
    Column("detected_count", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("error", Text),
)

explanation = Table(
    "explanation",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("detection_id", BigInteger, nullable=False),
    Column("round", Integer, nullable=False),
    Column("requested_by", BigInteger),  # NULL = 시스템 자동 요청
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("request_message", Text),
    Column("submitted_by", BigInteger),
    Column("submitted_at", DateTime(timezone=True)),
    Column("content", Text),
    Column("reviewed_by", BigInteger),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("review_result", String(16)),
    Column("review_comment", Text),
    Column("ticket_ids", ARRAY(String(32)), nullable=False),
)

setting = Table(
    "setting",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", JSONB, nullable=False),
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

explanation_attachment = Table(
    "explanation_attachment",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("explanation_id", BigInteger, nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("stored_path", String(500), nullable=False),
    Column("content_type", String(100), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", CHAR(64), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
)

# 점검 보고서 이력 (LOG-09, 0013) — v0.1은 마스킹 보고서만, 파일 대신 집계 스냅샷
inspection_report = Table(
    "inspection_report",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("period_from", DateTime(timezone=True), nullable=False),
    Column("period_to", DateTime(timezone=True), nullable=False),
    Column("scope", JSONB),
    Column("summary", JSONB, nullable=False),
    Column("escalated_count", Integer, nullable=False),
    Column("unmasked", Boolean, nullable=False),
    Column("unmask_reason", Text),
    Column("file_path", String(500)),
    Column("generated_by", BigInteger, nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
)
