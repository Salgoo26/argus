"""점검 보고서 API (기능 레이어 9 — LOG-09, §8② 점검 증적, policy 6-1)

- 정보보호 담당자만 만들고 본다(점검 업무). 취급자는 403
- 생성 = Argus 자체 접속기록 `EXPORT`(+ context.report_id, 보고서에 실린 마스킹 식별값 수)
  — 보고서는
  파일로 조직 밖에 나갈 수 있어 화면보다 통제가 더 중요하다(policy 6-1). 다시 열어 보는 것은 READ
- v0.1은 **마스킹 보고서만** — 언마스킹 보고서(사유 필수)는 언마스킹 기능과 함께 v0.2
- 파일은 서버에 만들지 않는다: 화면(인쇄용)이 스냅샷을 그리고, 담당자가 브라우저에서 PDF로 저장
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, model_validator
from sqlalchemy import insert, select

from app.agent import access_log, record_report, record_subject_count
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detection.rules import KST
from app.detections.transitions import OFFICER
from app.errors import ApiError
from app.models import argus_user, inspection_report
from app.reports.summary import build_summary, masked_count

router = APIRouter(prefix="/api/reports")

MAX_DAYS = 366  # 한 번에 1년 — 접속기록 보관기간(policy 5-1)


class ReportBody(BaseModel):
    date_from: date  # 한국 날짜, 양 끝 포함
    date_to: date
    # 전체 = 화면 경유·DB 직접을 한 보고서에 섹션으로, 하나만 고르면 그 경로만 (2026-10-07)
    scope: Literal["ALL", "APP", "DB"] = "ALL"

    @model_validator(mode="after")
    def _period(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        if (self.date_to - self.date_from).days >= MAX_DAYS:
            raise ValueError(f"period must be at most {MAX_DAYS} days")
        return self


def _officer_only(user: AuthenticatedUser) -> None:
    if user.role != OFFICER:
        raise ApiError(403, "FORBIDDEN", "inspection reports are for officers only")


def _bounds(body: ReportBody) -> tuple[datetime, datetime]:
    """[시작일 00:00, 종료일 다음날 00:00) — 한국 시각"""
    start = datetime.combine(body.date_from, time(0), KST)
    end = datetime.combine(body.date_to + timedelta(days=1), time(0), KST)
    return start, end


def _view(row) -> dict:
    return {
        "id": row["id"],
        "period_from": row["period_from"],
        "period_to": row["period_to"],
        "scope": (row["scope"] or {}).get("access_path", "ALL"),
        "escalated_count": row["escalated_count"],
        "unmasked": row["unmasked"],
        "generated_by": row["generated_by_login_id"],
        "generated_at": row["generated_at"],
    }


def _query():
    return select(inspection_report, argus_user.c.login_id.label("generated_by_login_id")).join(
        argus_user, argus_user.c.id == inspection_report.c.generated_by
    )


@router.post("", status_code=201)
@access_log(action="EXPORT", data_category="ACCESS_LOG")
def create_report(body: ReportBody, request: Request, user: CurrentUser) -> dict:
    _officer_only(user)
    start, end = _bounds(body)
    with request.app.state.engine.begin() as conn:
        summary = build_summary(conn, start, end, body.scope)
        escalated = sum(s["detections"]["escalated"] for s in summary["paths"].values())
        report_id = conn.execute(
            insert(inspection_report)
            .values(
                period_from=start,
                period_to=end,
                scope={"access_path": body.scope},
                summary=summary,
                escalated_count=escalated,
                unmasked=False,  # v0.1은 마스킹 보고서만 (policy 6-1)
                generated_by=user.id,
            )
            .returning(inspection_report.c.id)
        ).scalar_one()
        row = conn.execute(_query().where(inspection_report.c.id == report_id)).mappings().one()
    record_report(report_id)
    record_subject_count(masked_count(summary))
    return _view(row) | {"summary": summary}


@router.get("")
@access_log(action="READ", data_category="ACCESS_LOG")
def list_reports(
    request: Request,
    user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    _officer_only(user)
    size = 20
    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _query()
                .order_by(inspection_report.c.generated_at.desc(), inspection_report.c.id.desc())
                .limit(size)
                .offset((page - 1) * size)
            )
            .mappings()
            .all()
        )
    record_subject_count(0)  # 목록엔 식별값이 없다
    return {"items": [_view(r) for r in rows], "page": page, "size": size}


@router.get("/{report_id}")
@access_log(action="READ", data_category="ACCESS_LOG")
def get_report(report_id: int, request: Request, user: CurrentUser) -> dict:
    _officer_only(user)
    with request.app.state.engine.connect() as conn:
        row = conn.execute(_query().where(inspection_report.c.id == report_id)).mappings().first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "report not found")
    record_report(report_id)
    record_subject_count(masked_count(row["summary"]))
    return _view(row) | {"summary": row["summary"]}
