"""보호 대상 등록부 → 게이트웨이가 쓰는 판정 표 (v0.1 보강 N-3)

- 테이블의 데이터 유형 = 그 테이블에 등록된(활성) 개인정보 컬럼 중 가장 민감한 것
  (민감도 순서는 게이트웨이 sql.SENSITIVITY와 같다). 개인정보 컬럼이 하나도 없으면 표에 없다(NONE)
- 회원 식별 열 = 활성이고 member_key인 컬럼 — 2티어 결과에서 회원번호를 읽는 열(G-1)
- 버전 = 등록 이력의 마지막 id("r{id}") — 게이트웨이가 원장 context.registry_version에 남겨
  "그때 기준으로 왜 이렇게 분류됐는지"를 설명할 수 있게 한다
- v0.1 판정은 테이블 단위 — 컬럼 단위(개인정보 컬럼을 안 건드린 조회 제외)는 v0.2
"""

from typing import Any

from sqlalchemy import Connection, func, select

from app.models import protected_column, protected_column_history, source_system

DB = "platform"  # v0.1 대상 DB의 논리 이름 — 게이트웨이와 같은 값
SENSITIVITY = ("NONE", "ORDER", "INQUIRY", "MEMBER_BASIC", "PAYMENT")
# 고시 §8① — 고유식별정보·민감정보를 처리하면 접속기록 최소 2년 보관
TWO_YEAR_ITEMS = frozenset({"UNIQUE_ID", "SENSITIVE"})


def registry_version(conn: Connection) -> str:
    last = conn.execute(select(func.max(protected_column_history.c.id))).scalar_one()
    return f"r{last or 0}"


def active_columns(conn: Connection, source_code: str = "PLATFORM") -> list[Any]:
    return conn.execute(
        select(protected_column)
        .join(source_system, source_system.c.id == protected_column.c.source_system_id)
        .where(
            source_system.c.code == source_code,
            protected_column.c.db_name == DB,
            protected_column.c.active,
        )
        .order_by(protected_column.c.table_name, protected_column.c.column_name)
    ).all()


def table_categories(columns: list[Any]) -> dict[str, str]:
    tables: dict[str, str] = {}
    for column in columns:
        if column.data_category is None:  # 개인정보 아님
            continue
        current = tables.get(column.table_name, "NONE")
        tables[column.table_name] = max(current, column.data_category, key=SENSITIVITY.index)
    return tables


def build_registry(conn: Connection) -> dict[str, Any]:
    """게이트웨이에 내려주는 판정 표 — 값 없이 이름·분류만"""
    columns = active_columns(conn)
    return {
        "version": registry_version(conn),
        "database": DB,
        "tables": table_categories(columns),
        "member_columns": sorted([c.table_name, c.column_name] for c in columns if c.member_key),
    }
