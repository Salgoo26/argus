"""보호 대상 등록부 — 무통장입금·이체 환불 컬럼·테이블 등록 (플랫폼 0014)

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-21

플랫폼에 무통장입금(bank_deposit)과 취소·반품의 이체 환불 계좌 사본이 생겼다(커머스 확장 F7).
0023·0024와 같은 방식으로 컬럼 단위 등록 + 이력 사유(처리자 시스템).

- bank_deposit(신규, 데이터 유형 ORDER): 입금자명은 개인정보(이름).
  쇼핑몰 입금 계좌(은행·번호·예금주)는 쇼핑몰 자신의 계좌라 개인정보 아님.
  회원 열이 없어 주문을 거쳐서만 회원과 이어진다
- order_claim: 이체 환불 계좌(은행·예금주·계좌번호 암호문·끝 4자리)는 결제수단(PAYMENT) —
  refund_account와 같은 분류. 이 테이블의 데이터 유형이 ORDER → PAYMENT로 올라간다
  (테이블 데이터 유형 = 가장 민감한 컬럼). 환불 방식·상태·처리 시각·처리자는 개인정보 아님
- 이미 같은 컬럼이 등록돼 있으면(담당자가 화면에서 먼저 분류) 건너뛴다
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

DB = "platform"
REASON = "무통장입금·이체 환불 신규 컬럼 등록"

# (테이블, 데이터 유형, {컬럼: 항목}, 개인정보 아닌 컬럼)
_ROWS = (
    (
        "bank_deposit",
        "ORDER",
        {"depositor_name": "NAME"},
        (
            "order_id",
            "bank_name",
            "account_number",
            "account_holder",
            "due_at",
            "confirmed_at",
            "confirmed_by",
        ),
    ),
    (
        "order_claim",
        "PAYMENT",
        {
            "refund_bank_name": "ACCOUNT",
            "refund_account_holder": "NAME",
            "refund_account_enc": "ACCOUNT",
            "refund_account_last4": "ACCOUNT",
        },
        ("refund_method", "refund_status", "refunded_at", "refunded_by"),
    ),
)


def rows() -> list[dict]:
    result = []
    for table, category, personal, others in _ROWS:
        for column, item in personal.items():
            result.append(
                {
                    "table_name": table,
                    "column_name": column,
                    "item": item,
                    "data_category": category,
                    "member_key": False,
                }
            )
        for column in others:
            result.append(
                {
                    "table_name": table,
                    "column_name": column,
                    "item": "NOT_PERSONAL",
                    "data_category": None,
                    "member_key": False,
                }
            )
    return result


def upgrade() -> None:
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
    for row in rows():
        exists = conn.execute(
            sa.select(columns.c.id).where(
                columns.c.source_system_id == platform,
                columns.c.db_name == DB,
                columns.c.table_name == row["table_name"],
                columns.c.column_name == row["column_name"],
            )
        ).first()
        if exists is not None:
            continue
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
                reason=REASON,
            )
        )


def downgrade() -> None:
    # 등록부 이력은 append-only — 등록을 지우지 않는다(되돌리려면 담당자가 화면에서 해제)
    pass
