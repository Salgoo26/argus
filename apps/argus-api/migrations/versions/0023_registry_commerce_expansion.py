"""보호 대상 등록부 — 플랫폼 커머스 확장 컬럼·테이블 등록 (플랫폼 0011·0012)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-21

플랫폼에 배송·취소/반품 기록과 회원 본인확인 결과 컬럼이 생겼다. 등록하지 않으면 "미분류"로 남고
새 테이블(order_claim)은 DB 직접 접근 시 데이터 유형 NONE이 되어 탐지에서 빠진다
(architecture 3-4 "데이터 유형") → 컬럼 단위로 등록하고 이력에 사유를 남긴다(처리자 시스템).

- member: 아이디·CI·DI·생년월일·성별·내외국인·통신사는 개인정보, 인증 시각은 개인정보 아님
- orders: 송장번호는 개인정보(배송 건과 사람을 잇는다), 택배사·발송/도착 시각은 개인정보 아님
- order_claim: 데이터 유형 ORDER — 취소·반품 사유 상세(자유 입력)와 환불 거래번호.
  회원 열이 없어 주문을 거쳐서만 회원과 이어진다(payment와 같음)
- order_status_history: 개인정보 컬럼 없음 — 모두 "개인정보 아님"으로 분류(미분류로 남기지 않음)
- 이미 같은 컬럼이 등록돼 있으면(담당자가 화면에서 먼저 분류) 건너뛴다
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

DB = "platform"
REASON = "커머스 확장(배송·취소/반품·본인확인) 신규 컬럼 등록"

# (테이블, 데이터 유형, {컬럼: 항목}, 개인정보 아닌 컬럼)
_ROWS = (
    (
        "member",
        "MEMBER_BASIC",
        {
            "login_id": "OTHER",
            "ci": "OTHER",
            "di": "OTHER",
            "birth_date": "BIRTH",
            "gender": "OTHER",
            "is_foreigner": "OTHER",
            "carrier": "OTHER",
        },
        ("email_verified_at", "identity_verified_at"),
    ),
    ("orders", "ORDER", {"tracking_no": "OTHER"}, ("courier", "shipped_at", "delivered_at")),
    (
        "order_claim",
        "ORDER",
        {"reason_detail": "OTHER", "refund_pg_tid": "OTHER"},
        (
            "id",
            "order_id",
            "claim_type",
            "reason_code",
            "status",
            "requested_at",
            "completed_at",
            "completed_by",
            "refund_amount",
        ),
    ),
    (
        "order_status_history",
        None,
        {},
        ("id", "order_id", "from_status", "to_status", "actor_type", "operator_id", "changed_at"),
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
