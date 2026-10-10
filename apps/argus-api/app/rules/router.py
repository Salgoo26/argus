"""룰 빌더 API — 담당자 전용 (기능 레이어 6, LOG-04·13, policy 1-2)

담당자가 코드 배포 없이 탐지 룰을 만들고·고치고·켜고·끈다.
기준값 조정(몇 건, 몇 배, 몇 시)이 주 용도.

- GET  /api/rules?enabled=true|false   목록 (켜짐/꺼짐 필터, 룰별 탐지건 수)
- GET  /api/rules/{rule_id}            상세 + 변경 이력
- POST /api/rules                      생성
- PUT  /api/rules/{rule_id}            수정 — 유형(EVENT/AGGREGATE)은 바꾸지 않는다
- POST /api/rules/{rule_id}/enable     켜기
- POST /api/rules/{rule_id}/disable    끄기

결정 (2026-10-01 사용자와 논의):
- 담당자(OFFICER) 전원이 쓸 수 있다 — 룰 관리 전용 권한은 담당자가 여럿이 될 때
- **삭제는 없다** — 탐지건이 룰을 참조하고 "그 시점에 어떤 룰을 운영했나"가 점검 근거다.
  쓰지 않는 룰은 끄고, 목록의 켜짐/꺼짐 필터로 관리한다
- 사유는 받지 않는다 — 변경 이력에 **누가·언제·변경 후 룰 전체**가 남는다
- 저장할 때 탐지 배치와 **같은 검사기(validate_rule)** 로 확인 — 해석할 수 없는 룰은 저장 단계에서
  거부해, 켜진 룰 하나가 순찰 전체를 멈추는 일을 입구에서 막는다 (db-schema 3-7 #2)
- 동시 수정 방지: 화면이 본 버전(expected_version)을 함께 보내고, 그사이 바뀌었으면 409
- 고친 룰은 **다음 순찰부터** 적용된다. 과거 기록을 다시 판정하지 않는다(기준을 낮췄다고 1년치
  탐지건이 쏟아지지 않게). 집계 룰은 새 기록이 들어온 윈도우를 통째로 다시 세므로 그 안은 반영된다

접속기록: 룰 정의에는 정보주체가 없어 Argus 자체 접속기록 대상이 아니다.
변경은 룰 변경 이력에 남는다.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy import Connection, func, insert, select, update

from app.agent import access_log_exempt
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detection.rules import RuleError, validate_rule
from app.detections.transitions import OFFICER
from app.errors import ApiError
from app.models import argus_user, detection, detection_rule, detection_rule_history

router = APIRouter(prefix="/api/rules")

_EXEMPT = "룰 정의 — 정보주체 처리 없음. 변경은 detection_rule_history에 기록 (LOG-13)"
GROUP_BY = "ACTOR_RULE_DATE"  # 기본 그룹핑만 지원 (policy 2-2)
SNAPSHOT_KEYS = (
    "id",
    "name",
    "description",
    "rule_type",
    "access_path",
    "severity",
    "enabled",
    "auto_request",
    "condition",
    "aggregate",
    "group_by",
    "version",
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None


class RuleFields(BaseModel):
    """만들기·고치기에 공통인 항목 — 조건식·집계 스펙의 세부 형식은 validate_rule이 본다"""

    name: Name
    description: Description = None
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    access_path: Literal["APP", "DB", "ALL"] = "APP"
    auto_request: bool = True
    condition: dict[str, Any]
    aggregate: dict[str, Any] | None = None


class CreateBody(RuleFields):
    rule_type: Literal["EVENT", "AGGREGATE"]


class UpdateBody(RuleFields):
    expected_version: int  # 화면이 본 버전 — 그사이 다른 사람이 고쳤으면 409


class ToggleBody(BaseModel):
    expected_version: int


def _require_officer(user: AuthenticatedUser) -> None:
    if user.role != OFFICER:
        raise ApiError(403, "FORBIDDEN", "rules are managed by officers only")


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "rule not found")


def _validate(candidate: dict[str, Any]) -> None:
    try:
        validate_rule(candidate | {"group_by": GROUP_BY})
    except RuleError as error:
        # 화면이 막지 못한 형식 오류 — 어떤 부분이 문제인지 그대로 알려 준다
        raise ApiError(400, "INVALID_RULE", str(error)) from None


def _snapshot(row) -> dict[str, Any]:
    return {key: row[key] for key in SNAPSHOT_KEYS}


def _record(conn: Connection, row, change_type: str, user: AuthenticatedUser) -> None:
    conn.execute(
        insert(detection_rule_history).values(
            rule_id=row["id"],
            version=row["version"],
            change_type=change_type,
            snapshot=_snapshot(row),
            changed_by=user.id,
        )
    )


def _name_taken(conn: Connection, name: str, except_id: int | None = None) -> bool:
    query = select(detection_rule.c.id).where(detection_rule.c.name == name)
    if except_id is not None:
        query = query.where(detection_rule.c.id != except_id)
    return conn.execute(query).first() is not None


def _locked(conn: Connection, rule_id: int, expected_version: int):
    """행을 잠그고 버전 확인 — 동시에 고치는 두 사람 중 뒤의 사람은 409"""
    row = (
        conn.execute(select(detection_rule).where(detection_rule.c.id == rule_id).with_for_update())
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    if row["version"] != expected_version:
        raise ApiError(409, "VERSION_CONFLICT", "the rule was changed by someone else — reload")
    return row


def _save(conn: Connection, rule_id: int, values: dict[str, Any]):
    return (
        conn.execute(
            update(detection_rule)
            .where(detection_rule.c.id == rule_id)
            .values(**values, version=detection_rule.c.version + 1, updated_at=func.now())
            .returning(detection_rule)
        )
        .mappings()
        .one()
    )


# ── 조회 ──────────────────────────────────────────────────

_updated_by = (
    select(argus_user.c.login_id)
    .join(detection_rule_history, detection_rule_history.c.changed_by == argus_user.c.id)
    .where(
        detection_rule_history.c.rule_id == detection_rule.c.id,
        detection_rule_history.c.version == detection_rule.c.version,
    )
    .scalar_subquery()
)
_detection_count = (
    select(func.count())
    .select_from(detection)
    .where(detection.c.rule_id == detection_rule.c.id)
    .scalar_subquery()
)


def _summary(row) -> dict[str, Any]:
    return _snapshot(row) | {
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],  # None = 시스템(마이그레이션 시드)
        "detection_count": row["detection_count"],
    }


def _rule_query():
    return select(
        detection_rule,
        _updated_by.label("updated_by"),
        _detection_count.label("detection_count"),
    )


@router.get("")
@access_log_exempt(_EXEMPT)
def list_rules(
    request: Request,
    user: CurrentUser,
    enabled: Annotated[bool | None, Query()] = None,
    # v0.1 보강 C-3 — 룰 정의는 개인정보가 아니라 URL 쿼리로 받는다
    name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    access_path: Annotated[Literal["APP", "DB", "ALL"] | None, Query()] = None,
    severity: Annotated[Literal["HIGH", "MEDIUM", "LOW"] | None, Query()] = None,
    rule_type: Annotated[Literal["EVENT", "AGGREGATE"] | None, Query()] = None,
) -> dict:
    _require_officer(user)
    r = detection_rule.c
    query = _rule_query().order_by(r.id)
    for column, value in (
        (r.enabled, enabled),
        (r.access_path, access_path),
        (r.severity, severity),
        (r.rule_type, rule_type),
    ):
        if value is not None:
            query = query.where(column == value)
    if name:
        # 부분 일치 — 입력의 % _ 는 글자 그대로 (autoescape)
        query = query.where(r.name.contains(name, autoescape=True))
    with request.app.state.engine.connect() as conn:
        items = [_summary(r) for r in conn.execute(query).mappings()]
    return {"items": items}


@router.get("/{rule_id}")
@access_log_exempt(_EXEMPT)
def get_rule(rule_id: int, request: Request, user: CurrentUser) -> dict:
    _require_officer(user)
    with request.app.state.engine.connect() as conn:
        row = conn.execute(_rule_query().where(detection_rule.c.id == rule_id)).mappings().first()
        if row is None:
            raise _not_found()
        history = conn.execute(
            select(
                detection_rule_history.c.version,
                detection_rule_history.c.change_type,
                detection_rule_history.c.snapshot,
                detection_rule_history.c.changed_at,
                argus_user.c.login_id.label("changed_by"),
            )
            .outerjoin(argus_user, argus_user.c.id == detection_rule_history.c.changed_by)
            .where(detection_rule_history.c.rule_id == rule_id)
            .order_by(detection_rule_history.c.version.desc())
        ).mappings()
        return _summary(row) | {"history": [dict(h) for h in history]}


# ── 변경 ──────────────────────────────────────────────────


@router.post("", status_code=201)
@access_log_exempt(_EXEMPT)
def create_rule(body: CreateBody, request: Request, user: CurrentUser) -> dict:
    _require_officer(user)
    values = body.model_dump()
    _validate(values)
    with request.app.state.engine.begin() as conn:
        if _name_taken(conn, body.name):
            raise ApiError(409, "DUPLICATE_NAME", "a rule with this name already exists")
        row = (
            conn.execute(
                insert(detection_rule)
                .values(**values, group_by=GROUP_BY, enabled=True, created_by=user.id)
                .returning(detection_rule)
            )
            .mappings()
            .one()
        )
        _record(conn, row, "CREATE", user)
    return _snapshot(row)


@router.put("/{rule_id}")
@access_log_exempt(_EXEMPT)
def update_rule(rule_id: int, body: UpdateBody, request: Request, user: CurrentUser) -> dict:
    _require_officer(user)
    values = body.model_dump(exclude={"expected_version"})
    with request.app.state.engine.begin() as conn:
        current = _locked(conn, rule_id, body.expected_version)
        _validate(values | {"rule_type": current["rule_type"]})  # 유형은 바꾸지 않는다
        if _name_taken(conn, body.name, except_id=rule_id):
            raise ApiError(409, "DUPLICATE_NAME", "a rule with this name already exists")
        if all(current[key] == value for key, value in values.items()):
            return _snapshot(current)  # 바뀐 것이 없으면 버전·이력을 늘리지 않는다
        row = _save(conn, rule_id, values)
        _record(conn, row, "UPDATE", user)
    return _snapshot(row)


def _toggle(request: Request, user: AuthenticatedUser, rule_id: int, body: ToggleBody, on: bool):
    _require_officer(user)
    with request.app.state.engine.begin() as conn:
        current = _locked(conn, rule_id, body.expected_version)
        if current["enabled"] == on:
            return _snapshot(current)  # 이미 그 상태
        if on:
            # 꺼 둔 사이 코드가 바뀌어 해석할 수 없게 됐을 수 있다 — 켜기 전에 다시 확인
            _validate(dict(current))
        row = _save(conn, rule_id, {"enabled": on})
        _record(conn, row, "ENABLE" if on else "DISABLE", user)
    return _snapshot(row)


@router.post("/{rule_id}/enable")
@access_log_exempt(_EXEMPT)
def enable_rule(rule_id: int, body: ToggleBody, request: Request, user: CurrentUser) -> dict:
    return _toggle(request, user, rule_id, body, on=True)


@router.post("/{rule_id}/disable")
@access_log_exempt(_EXEMPT)
def disable_rule(rule_id: int, body: ToggleBody, request: Request, user: CurrentUser) -> dict:
    return _toggle(request, user, rule_id, body, on=False)
