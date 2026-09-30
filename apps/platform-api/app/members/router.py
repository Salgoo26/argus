"""회원 목록·다운로드 (PLT-11, PLT-13) — 감시 대상 관리자 기능

접속기록(Agent)은 M2 PR ③에서 붙인다. 이 파일은 업무 기능만 담는다.
"""

import csv
import io
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app.auth.deps import CurrentOperator
from app.models import member

router = APIRouter(prefix="/admin/members")

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 오프셋으로 충분
EXPORT_MAX_ROWS = 10_000
CSV_COLUMNS = ("id", "name", "email", "phone", "address", "joined_at")
# 엑셀이 수식으로 해석하는 시작 문자 (OWASP "CSV Injection")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    # 셀이 수식으로 실행되지 않도록 앞에 '를 붙여 문자열로 고정
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


@router.get("")
def list_members(
    request: Request,
    _operator: CurrentOperator,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    with request.app.state.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(member)).scalar_one()
        rows = conn.execute(
            select(
                member.c.id,
                member.c.name,
                member.c.email,
                member.c.phone,
                member.c.status,
                member.c.created_at,
            )
            .order_by(member.c.id)
            .limit(size)
            .offset((page - 1) * size)
        ).mappings()
        items = [dict(r) for r in rows]
    return {"items": items, "page": page, "size": size, "total": total}


@router.get("/export")
def export_members(
    request: Request,
    _operator: CurrentOperator,
    joined_from: date | None = None,
    joined_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=EXPORT_MAX_ROWS)] = EXPORT_MAX_ROWS,
) -> Response:
    """활동 중인 회원을 가입일 조건으로 골라 CSV로 내려준다.

    joined_from·joined_to는 한국 날짜 기준이고 양 끝을 포함한다.
    """
    query = select(
        member.c.id,
        member.c.name,
        member.c.email,
        member.c.phone,
        member.c.address,
        member.c.created_at,
    ).where(member.c.status == "ACTIVE")
    if joined_from is not None:
        query = query.where(member.c.created_at >= datetime.combine(joined_from, time(), KST))
    if joined_to is not None:
        next_day = datetime.combine(joined_to + timedelta(days=1), time(), KST)
        query = query.where(member.c.created_at < next_day)

    with request.app.state.engine.connect() as conn:
        rows = conn.execute(query.order_by(member.c.id).limit(limit)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for member_id, name, email, phone, address, created_at in rows:
        joined_at = created_at.astimezone(KST).isoformat(timespec="seconds")
        writer.writerow([member_id, *map(_csv_safe, (name, email, phone, address)), joined_at])

    filename = f"members_{datetime.now(UTC).astimezone(KST):%Y%m%d_%H%M%S}.csv"
    return Response(
        # BOM(utf-8-sig): 엑셀이 한글을 깨뜨리지 않고 UTF-8로 인식하게
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
