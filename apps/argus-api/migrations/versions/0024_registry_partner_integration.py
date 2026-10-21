"""보호 대상 등록부 — 가상 PG·택배 연동 컬럼 등록 (플랫폼 0013)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-21

플랫폼 주문·반품·결제에 PG 승인 정보와 택배 접수 정보 컬럼이 생겼다(커머스 확장 F6).
0023과 같은 방식으로 컬럼 단위 등록 + 이력 사유(처리자 시스템). 새 테이블은 없다.

- orders: 배송 메모(자유 입력)·택배 접수 번호는 개인정보(배송 건과 사람을 잇는다),
  택배 상태·동기화 시각은 개인정보 아님
- order_claim: 반품 회수 접수 번호·회수 송장번호는 개인정보, 회수 상태는 개인정보 아님
- payment: 마스킹 카드번호(앞 6·뒤 4자리)는 항목 CARD, 승인번호·영수증 주소는 OTHER.
  데이터 유형은 기존 payment 컬럼과 같은 ORDER — 카드 전체 번호가 아니라 PG가 돌려준 거래
  정보이고, 화면(주문 상세)도 주문 조회로 기록한다. 할부 개월은 개인정보 아님
- 이미 같은 컬럼이 등록돼 있으면(담당자가 화면에서 먼저 분류) 건너뛴다
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

DB = "platform"
REASON = "가상 PG·택배 연동 신규 컬럼 등록"

# (테이블, 데이터 유형, {컬럼: 항목}, 개인정보 아닌 컬럼)
_ROWS = (
    (
        "orders",
        "ORDER",
        {"delivery_memo": "OTHER", "courier_shipment_id": "OTHER"},
        ("courier_status", "courier_synced_at"),
    ),
    (
        "order_claim",
        "ORDER",
        {"pickup_shipment_id": "OTHER", "pickup_tracking_no": "OTHER"},
        ("pickup_status",),
    ),
    (
        "payment",
        "ORDER",
        {"card_number_masked": "CARD", "approve_no": "OTHER", "receipt_url": "OTHER"},
        ("installment_months",),
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
