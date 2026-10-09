"""보호 대상 등록부 + DB 구조 목록 (v0.1 보강 N — 개인정보 식별·관리)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-10

점검의 출발점 "무엇이 보호 대상인가"를 담당자가 보고 고칠 수 있게, 게이트웨이 코드에 고정돼 있던
테이블 → 데이터 유형 표(db-gateway sql.TABLE_CATEGORY)와 회원 식별 열(catalog.MEMBER_COLUMNS)을
Argus의 등록부로 옮긴다.

- protected_column: 시스템(출처) → DB → 테이블 → 컬럼마다 개인정보 항목·데이터 유형·회원 식별 열.
  "개인정보 아님"(NOT_PERSONAL)도 명시적으로 분류한다 — 분류하지 않은 컬럼은 "미분류"로 드러난다.
  해제는 삭제가 아니라 비활성(active=false)
- protected_column_history: 등록·변경·해제 이력(사유 필수, append-only) — 룰 변경 이력과 같은 방식.
  등록부 버전 = 이 이력의 마지막 id("r{id}") — 게이트웨이가 원장 context에 남긴다
- db_schema_column·db_schema_receipt: 게이트웨이가 보낸 DB 구조 목록(테이블·컬럼 이름과 자료형만,
  데이터 값 없음)과 수신 기록. 받을 때마다 그 DB의 목록을 통째로 바꾼다
- 【기본값】 초기 등록: 지금 코드에 고정된 표를 컬럼 단위로 옮기고 이력에 "초기 등록"
  (처리자 시스템).
  고정 표의 8개 테이블은 컬럼을 모두 분류(개인정보 / 개인정보 아님)하고, 나머지 테이블은
  미분류로 둔다
  — 담당자가 화면에서 분류한다
- 앱 계정: 등록부는 조회·추가·수정, 이력은 조회·추가만, 구조 목록은 수신 때 통째로
  바꾸므로 삭제 포함
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

DB = "platform"  # v0.1 대상은 플랫폼 DB 하나 — 게이트웨이와 같은 논리 이름(app/registry.py)
ITEMS = (
    "NAME",  # 이름
    "EMAIL",  # 이메일
    "PHONE",  # 연락처
    "ADDRESS",  # 주소
    "BIRTH",  # 생년월일
    "ACCOUNT",  # 계좌번호
    "CARD",  # 카드번호
    "UNIQUE_ID",  # 고유식별정보
    "SENSITIVE",  # 민감정보
    "CREDENTIAL",  # 인증정보(비밀번호 등)
    "MEMBER_ID",  # 회원 식별자(내부번호)
    "OTHER",  # 기타 개인정보
    "NOT_PERSONAL",  # 개인정보 아님
)

# 고정 표(TABLE_CATEGORY·MEMBER_COLUMNS)의 컬럼 단위 이관 — (테이블, 데이터 유형, {컬럼: 항목})
# 회원 식별 열 = 항목 MEMBER_ID. 적지 않은 컬럼은 NOT_PERSONAL
_SEED = (
    (
        "member",
        "MEMBER_BASIC",
        {
            "id": "MEMBER_ID",
            "email": "EMAIL",
            "password_hash": "CREDENTIAL",
            "name": "NAME",
            "phone": "PHONE",
        },
        ("status", "created_at", "withdrawn_at", "failed_login_count", "locked_until"),
    ),
    (
        "member_consent",
        "MEMBER_BASIC",
        {"member_id": "MEMBER_ID", "client_ip": "OTHER"},
        ("id", "item_code", "item_version", "agreed", "acted_at", "method"),
    ),
    (
        "retained_member_record",
        "MEMBER_BASIC",
        {"original_member_id": "MEMBER_ID", "data": "OTHER"},
        ("id", "retain_reason", "legal_basis", "retain_until", "created_at"),
    ),
    (
        "shipping_address",
        "MEMBER_BASIC",
        {
            "member_id": "MEMBER_ID",
            "recipient": "NAME",
            "phone": "PHONE",
            "zip_code": "ADDRESS",
            "address": "ADDRESS",
            "address_detail": "ADDRESS",
        },
        ("id", "label", "is_default", "created_at", "updated_at"),
    ),
    (
        "refund_account",
        "PAYMENT",
        {
            "member_id": "MEMBER_ID",
            "bank_name": "ACCOUNT",
            "account_holder": "NAME",
            "account_number_enc": "ACCOUNT",
            "account_last4": "ACCOUNT",
        },
        ("id", "created_at", "updated_at"),
    ),
    (
        "inquiry",
        "INQUIRY",
        {"member_id": "MEMBER_ID", "title": "OTHER", "body": "OTHER", "answer": "OTHER"},
        ("id", "status", "answered_by", "created_at", "answered_at"),
    ),
    (
        "orders",
        "ORDER",
        {
            "member_id": "MEMBER_ID",
            "ship_recipient": "NAME",
            "ship_phone": "PHONE",
            "ship_zip_code": "ADDRESS",
            "ship_address": "ADDRESS",
            "ship_address_detail": "ADDRESS",
        },
        ("id", "product_id", "amount", "status", "ordered_at"),
    ),
    (
        # PG 거래 정보 — 회원 열은 없고 주문을 거쳐서만 회원과 이어진다
        "payment",
        "ORDER",
        {"card_company": "OTHER", "pg_tid": "OTHER"},
        ("id", "order_id", "method", "amount", "approved_at"),
    ),
)

UPGRADE_SQL = """
CREATE TABLE protected_column (
    id                bigserial    PRIMARY KEY,
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    db_name           varchar(63)  NOT NULL,
    table_name        varchar(63)  NOT NULL,
    column_name       varchar(63)  NOT NULL,
    item              varchar(16)  NOT NULL,
    data_category     varchar(16),
    member_key        boolean      NOT NULL DEFAULT false,
    active            boolean      NOT NULL DEFAULT true,
    updated_by        bigint       REFERENCES argus_user(id),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (source_system_id, db_name, table_name, column_name),
    CHECK (item IN ('NAME','EMAIL','PHONE','ADDRESS','BIRTH','ACCOUNT','CARD','UNIQUE_ID',
                    'SENSITIVE','CREDENTIAL','MEMBER_ID','OTHER','NOT_PERSONAL')),
    CHECK ((item = 'NOT_PERSONAL') = (data_category IS NULL)),
    CHECK (data_category IS NULL OR data_category IN ('MEMBER_BASIC','PAYMENT','ORDER','INQUIRY')),
    CHECK (NOT member_key OR item = 'MEMBER_ID')
);

CREATE TABLE protected_column_history (
    id            bigserial    PRIMARY KEY,
    column_id     bigint       NOT NULL REFERENCES protected_column(id),
    change_type   varchar(8)   NOT NULL CHECK (change_type IN ('REGISTER','CHANGE','RELEASE')),
    snapshot      jsonb        NOT NULL,
    reason        varchar(500) NOT NULL CHECK (length(btrim(reason)) > 0),
    changed_by    bigint       REFERENCES argus_user(id),
    changed_at    timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_protected_column_history_column ON protected_column_history (column_id, id);

CREATE FUNCTION forbid_protected_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'protected_column_history is append-only (% blocked)', TG_OP; END $$;
CREATE TRIGGER trg_protected_history_append_only
    BEFORE UPDATE OR DELETE ON protected_column_history
    FOR EACH ROW EXECUTE FUNCTION forbid_protected_history_change();

CREATE TABLE db_schema_column (
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    db_name           varchar(63)  NOT NULL,
    table_name        varchar(63)  NOT NULL,
    column_name       varchar(63)  NOT NULL,
    data_type         varchar(64)  NOT NULL,
    ordinal           int          NOT NULL,
    PRIMARY KEY (source_system_id, db_name, table_name, column_name)
);

CREATE TABLE db_schema_receipt (
    id                bigserial    PRIMARY KEY,
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    db_name           varchar(63)  NOT NULL,
    table_count       int          NOT NULL,
    column_count      int          NOT NULL,
    received_at       timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_db_schema_receipt_received ON db_schema_receipt (received_at DESC);

GRANT SELECT, INSERT, UPDATE ON protected_column TO argus_app;
GRANT SELECT, INSERT ON protected_column_history TO argus_app;
GRANT SELECT, INSERT, DELETE ON db_schema_column TO argus_app;
GRANT SELECT, INSERT ON db_schema_receipt TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE protected_column_id_seq, protected_column_history_id_seq,
    db_schema_receipt_id_seq TO argus_app;
"""

DOWNGRADE_SQL = """
DROP TABLE db_schema_receipt;
DROP TABLE db_schema_column;
DROP TABLE protected_column_history;
DROP FUNCTION forbid_protected_history_change();
DROP TABLE protected_column;
"""


def seed_rows() -> list[dict]:
    rows = []
    for table, category, personal, others in _SEED:
        for column, item in personal.items():
            rows.append(
                {
                    "table_name": table,
                    "column_name": column,
                    "item": item,
                    "data_category": category,
                    "member_key": item == "MEMBER_ID",
                }
            )
        for column in others:
            rows.append(
                {
                    "table_name": table,
                    "column_name": column,
                    "item": "NOT_PERSONAL",
                    "data_category": None,
                    "member_key": False,
                }
            )
    return rows


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    conn = op.get_bind()
    platform = conn.execute(
        sa.text("SELECT id FROM source_system WHERE code = 'PLATFORM'")
    ).scalar_one()
    columns = sa.table(
        "protected_column",
        sa.column("id", sa.BigInteger),
        sa.column("source_system_id", sa.SmallInteger),
        sa.column("db_name", sa.String),
        sa.column("table_name", sa.String),
        sa.column("column_name", sa.String),
        sa.column("item", sa.String),
        sa.column("data_category", sa.String),
        sa.column("member_key", sa.Boolean),
    )
    history = sa.table(
        "protected_column_history",
        sa.column("column_id", sa.BigInteger),
        sa.column("change_type", sa.String),
        sa.column("snapshot", sa.JSON),
        sa.column("reason", sa.String),
    )
    for row in seed_rows():
        column_id = conn.execute(
            sa.insert(columns)
            .values(source_system_id=platform, db_name=DB, **row)
            .returning(columns.c.id)
        ).scalar_one()
        conn.execute(
            sa.insert(history).values(
                column_id=column_id,
                change_type="REGISTER",
                snapshot={**row, "db_name": DB, "active": True},
                reason="초기 등록 — 게이트웨이 고정 표 이관",
            )
        )


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
