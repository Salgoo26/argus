"""화면 알림 API — 헤더 배지·알림 목록 (v0.1 보강 F-2)

- GET  /api/notifications            내 알림 최근 30개 + 안 읽은 수 (화면이 30초마다 확인)
- POST /api/notifications/{id}/read  읽음 표시 (내 알림만)
- POST /api/notifications/read-all   모두 읽음

본인 알림만 다룬다 — 다른 사람의 알림 번호는 404(존재 여부를 드러내지 않음).
응답에는 종류·탐지건 번호·룰 이름·심각도·차수만 — 회원번호·취급자 이름은 싣지 않는다.
자체 접속기록에서 제외: 정보주체를 처리하지 않고, 30초 확인이 원장을 채우지 않게(아래 사유).
알림이 가리키는 탐지건을 열면 그 조회가 READ로 남는다.
"""

from fastapi import APIRouter, Request
from sqlalchemy import func, select, update

from app.agent import access_log_exempt
from app.auth.deps import CurrentUser
from app.errors import ApiError
from app.models import detection, notification

router = APIRouter(prefix="/api/notifications")

LIST_SIZE = 30
_EXEMPT = "본인 알림(탐지건 번호·룰 이름·심각도) — 정보주체 처리 없음, 탐지건을 열면 READ로 기록"


@router.get("")
@access_log_exempt(_EXEMPT)
def list_notifications(request: Request, user: CurrentUser) -> dict:
    n = notification.c
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(
            select(
                n.id,
                n.kind,
                n.detection_id,
                n.severity,
                n.round,
                n.created_at,
                n.read_at,
                detection.c.rule_snapshot["name"].as_string().label("rule_name"),
            )
            .join(detection, detection.c.id == n.detection_id)
            .where(n.user_id == user.id)
            .order_by(n.created_at.desc(), n.id.desc())
            .limit(LIST_SIZE)
        ).mappings()
        items = [dict(r) for r in rows]
        unread = conn.execute(
            select(func.count()).where(n.user_id == user.id, n.read_at.is_(None))
        ).scalar_one()
    return {"items": items, "unread": unread}


@router.post("/read-all", status_code=204)
@access_log_exempt(_EXEMPT)
def read_all(request: Request, user: CurrentUser) -> None:
    with request.app.state.engine.begin() as conn:
        conn.execute(
            update(notification)
            .where(notification.c.user_id == user.id, notification.c.read_at.is_(None))
            .values(read_at=func.now())
        )


@router.post("/{notification_id}/read", status_code=204)
@access_log_exempt(_EXEMPT)
def read_one(notification_id: int, request: Request, user: CurrentUser) -> None:
    with request.app.state.engine.begin() as conn:
        updated = conn.execute(
            update(notification)
            .where(notification.c.id == notification_id, notification.c.user_id == user.id)
            .values(read_at=func.coalesce(notification.c.read_at, func.now()))
        ).rowcount
    if not updated:
        raise ApiError(404, "NOT_FOUND", "notification not found")
