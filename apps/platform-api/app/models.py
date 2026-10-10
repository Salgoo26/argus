"""SQLAlchemy 테이블 정의 — 코드에서 쓰는 테이블만, 쓰는 시점에 추가한다.

스키마의 원본은 마이그레이션(=docs/db-schema.md 4절 DDL)이다. 여기 정의는 쿼리 작성용이며
CHECK·인덱스 등 제약은 옮기지 않는다 (argus-api와 같은 방식).
"""

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import BYTEA, INET, JSONB

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
    # 계정 관리 화면이 만든 임시 비밀번호 — 첫 로그인 때 변경 강제 (v0.1 보강 L-2)
    Column("must_change_password", Boolean, nullable=False),
)

member = Table(
    "member",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("email", String(255), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("name", String(50), nullable=False),
    Column("phone", String(20)),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("withdrawn_at", DateTime(timezone=True)),
    Column("failed_login_count", Integer, nullable=False),
    Column("locked_until", DateTime(timezone=True)),
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

consent_item = Table(
    "consent_item",
    metadata,
    Column("code", String(32), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("required", Boolean, nullable=False),
    Column("version", String(16), nullable=False),
    Column("purpose", Text, nullable=False),
    Column("items", Text, nullable=False),
    Column("retention", Text, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
)

member_consent = Table(
    "member_consent",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("member_id", BigInteger, ForeignKey("member.id"), nullable=False),
    Column("item_code", String(32), ForeignKey("consent_item.code"), nullable=False),
    Column("item_version", String(16), nullable=False),
    Column("agreed", Boolean, nullable=False),
    Column("acted_at", DateTime(timezone=True), nullable=False),
    Column("client_ip", INET),
    Column("method", String(16), nullable=False),
)

product = Table(
    "product",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("price", Integer, nullable=False),
)

orders = Table(
    "orders",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("member_id", BigInteger, ForeignKey("member.id")),
    Column("product_id", BigInteger, ForeignKey("product.id"), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("ordered_at", DateTime(timezone=True), nullable=False),
    # 배송 정보 스냅샷 — 주문 시점 배송지를 복사 (0008)
    Column("ship_recipient", String(50)),
    Column("ship_phone", String(20)),
    Column("ship_zip_code", String(5)),
    Column("ship_address", String(255)),
    Column("ship_address_detail", String(100)),
)

# 주문의 배송 정보 스냅샷 컬럼 (탈퇴 시 분리보관으로 옮기고 비운다)
SHIP_COLUMNS = (
    "ship_recipient",
    "ship_phone",
    "ship_zip_code",
    "ship_address",
    "ship_address_detail",
)

payment = Table(
    "payment",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", BigInteger, ForeignKey("orders.id"), nullable=False),
    Column("method", String(16), nullable=False),
    Column("card_company", String(30), nullable=False),
    Column("pg_tid", String(64), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("approved_at", DateTime(timezone=True), nullable=False),
)

refund_account = Table(
    "refund_account",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("member_id", BigInteger, ForeignKey("member.id"), nullable=False),
    Column("bank_name", String(50), nullable=False),
    Column("account_holder", String(50), nullable=False),
    Column("account_number_enc", BYTEA, nullable=False),
    Column("account_last4", CHAR(4), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

retained_member_record = Table(
    "retained_member_record",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("original_member_id", BigInteger, nullable=False),
    Column("retain_reason", String(64), nullable=False),
    Column("legal_basis", String(100), nullable=False),
    Column("data", JSONB, nullable=False),
    Column("retain_until", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

destruction_history = Table(
    "destruction_history",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("executed_at", DateTime(timezone=True), nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("cutoff_at", DateTime(timezone=True), nullable=False),
    Column("deleted_count", Integer, nullable=False),
    Column("legal_basis", String(100), nullable=False),
)

inquiry = Table(
    "inquiry",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("member_id", BigInteger, ForeignKey("member.id")),
    Column("title", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("answer", Text),
    Column("answered_by", BigInteger, ForeignKey("operator.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("answered_at", DateTime(timezone=True)),
)

# DB 접속 토큰 발급 기록 — 감사용, 토큰 값은 저장하지 않는다 (2티어, 0005)
db_access_token = Table(
    "db_access_token",
    metadata,
    Column("token_id", Uuid, primary_key=True),
    Column("operator_id", BigInteger, ForeignKey("operator.id"), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("issued_ip", INET, nullable=False),
)

# 배송지 — 회원별 여러 개, 기본 배송지 1개 (기능 레이어 7-4 ②, 0007)
shipping_address = Table(
    "shipping_address",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("member_id", BigInteger, ForeignKey("member.id"), nullable=False),
    Column("label", String(30), nullable=False),
    Column("recipient", String(50), nullable=False),
    Column("phone", String(20)),
    Column("zip_code", String(5)),
    Column("address", String(255), nullable=False),
    Column("address_detail", String(100)),
    Column("is_default", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# 개인정보취급자 권한 부여·변경·말소 이력 — append-only, 최소 3년 (v0.1 보강 L-3, §5③)
operator_permission_history = Table(
    "operator_permission_history",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("operator_id", BigInteger, nullable=False),
    Column("change_type", String(8), nullable=False),  # GRANT / CHANGE / REVOKE
    Column("before_role", String(16)),
    Column("after_role", String(16), nullable=False),
    Column("before_team", String(50)),
    Column("after_team", String(50), nullable=False),
    Column("before_status", String(16)),
    Column("after_status", String(16), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("actor_id", BigInteger),  # NULL = 시스템(시드·마이그레이션)
    Column("created_at", DateTime(timezone=True), nullable=False),
)
