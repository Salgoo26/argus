"""게이트웨이 ↔ Argus — DB 구조 목록 수신·보호 대상 등록부 제공 (v0.1 보강 N-2·N-3)

- POST /ingest/v1/db-schema            게이트웨이 → Argus: 테이블·컬럼 **이름과 자료형만**
- GET  /ingest/v1/protection-registry  Argus → 게이트웨이: 테이블 → 데이터 유형, 회원 식별 열, 버전

둘 다 수집 API와 같은 HMAC 서명(출처 PLATFORM — 게이트웨이는 relay와 같은 키). 구조 목록은
키를 정확히 정해 두고 그 밖의 키(값이 실릴 수 있는 칸)는 거부한다 — 데이터 값이 Argus로 오지 않게
(절대 규칙 #3의 수신 측 방어).
"""

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, insert, select

from app.errors import ApiError
from app.ingest.auth import VerifiedRequest, verify_signed_request
from app.models import db_schema_column, db_schema_receipt, source_system
from app.protection.registry import DB, build_registry

router = APIRouter(prefix="/ingest/v1")

SOURCE = "PLATFORM"
MAX_TABLES = 500
MAX_COLUMNS = 5000
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_TYPE = re.compile(r"^[A-Za-z0-9_ (),\[\].]{1,64}$")  # format_type 결과 (character varying(255) 등)


def _bad(message: str) -> ApiError:
    return ApiError(400, "INVALID_SCHEMA", message)


def _only_platform(verified: VerifiedRequest) -> None:
    if verified.source_code != SOURCE:
        raise ApiError(403, "FORBIDDEN", "only the platform gateway may use this endpoint")


def parse_schema(body: bytes) -> tuple[str, list[tuple[str, str, str, int]]]:
    """{"database": "platform", "tables": [{"name", "columns": [{"name", "type"}]}]}
    → (DB 이름, [(테이블, 컬럼, 자료형, 순서)])"""
    try:
        payload: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(400, "MALFORMED_JSON", "request body is not valid JSON") from None
    if not isinstance(payload, dict) or set(payload) != {"database", "tables"}:
        raise _bad("body must be {database, tables}")
    if payload["database"] != DB:
        raise _bad(f"unknown database (v0.1 accepts {DB!r} only)")
    tables = payload["tables"]
    if not isinstance(tables, list) or len(tables) > MAX_TABLES:
        raise _bad(f"tables must be a list of at most {MAX_TABLES}")
    rows: list[tuple[str, str, str, int]] = []
    for table in tables:
        if not isinstance(table, dict) or set(table) != {"name", "columns"}:
            raise _bad("table must be {name, columns}")
        if not isinstance(table["name"], str) or not _NAME.fullmatch(table["name"]):
            raise _bad("invalid table name")
        if not isinstance(table["columns"], list):
            raise _bad("columns must be a list")
        for ordinal, column in enumerate(table["columns"], start=1):
            if not isinstance(column, dict) or set(column) != {"name", "type"}:
                raise _bad("column must be {name, type}")  # 값·예시 칸 거부
            name, type_ = column["name"], column["type"]
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise _bad("invalid column name")
            if not isinstance(type_, str) or not _TYPE.fullmatch(type_):
                raise _bad("invalid column type")
            rows.append((table["name"], name, type_, ordinal))
    if len(rows) > MAX_COLUMNS:
        raise _bad(f"at most {MAX_COLUMNS} columns")
    if len({(t, c) for t, c, _, _ in rows}) != len(rows):
        raise _bad("duplicate column")
    return payload["database"], rows


@router.post("/db-schema")
def ingest_db_schema(
    request: Request, verified: Annotated[VerifiedRequest, Depends(verify_signed_request)]
) -> dict:
    """구조 목록을 통째로 바꾼다 — 사라진 테이블·컬럼은 목록에서도 사라진다(등록부는 그대로)"""
    _only_platform(verified)
    db_name, rows = parse_schema(verified.body)
    with request.app.state.engine.begin() as conn:
        source_id = conn.execute(
            select(source_system.c.id).where(source_system.c.code == SOURCE)
        ).scalar_one()
        conn.execute(
            delete(db_schema_column).where(
                db_schema_column.c.source_system_id == source_id,
                db_schema_column.c.db_name == db_name,
            )
        )
        if rows:
            conn.execute(
                insert(db_schema_column),
                [
                    {
                        "source_system_id": source_id,
                        "db_name": db_name,
                        "table_name": t,
                        "column_name": c,
                        "data_type": type_,
                        "ordinal": ordinal,
                    }
                    for t, c, type_, ordinal in rows
                ],
            )
        conn.execute(
            insert(db_schema_receipt).values(
                source_system_id=source_id,
                db_name=db_name,
                table_count=len({t for t, *_ in rows}),
                column_count=len(rows),
            )
        )
    return {"tables": len({t for t, *_ in rows}), "columns": len(rows)}


@router.get("/protection-registry")
def get_protection_registry(
    request: Request, verified: Annotated[VerifiedRequest, Depends(verify_signed_request)]
) -> dict:
    _only_platform(verified)
    with request.app.state.engine.connect() as conn:
        return build_registry(conn)
