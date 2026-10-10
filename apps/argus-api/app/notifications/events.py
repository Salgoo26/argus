"""알림 만들기 — 누가 어떤 사건을 받는가 (v0.1 보강 F-1, 안내서 129쪽 "이상행위 탐지 시 알림")

사건 → 받는 사람 (심각도 상 / 중 / 하):
- 탐지건 생성 DETECTED → 담당자 전원 (화면+푸시 / 없음 / 없음)
- 소명 요청 REQUESTED(자동·수동·재요청) → 해당 취급자 (화면+푸시 / 화면 / 화면)
- 기한 임박 DUE_SOON·초과 OVERDUE → 취급자 + 담당자 전원 (화면+푸시 / 화면+푸시 / 화면)
- 소명 제출 SUBMITTED → 요청한 담당자, 자동 요청이면 담당자 전원 (화면 / 화면 / 화면)
(푸시는 F-4 — 화면 알림과 별개의 추가 수단)

- 탐지건 생성은 **상만** — 중·하까지 알리면 담당자 알림이 너무 많아 목록에서만 본다
  (사용자 결정 10/10)
- 알림에는 본문이 없다 — 종류·탐지건 번호·심각도·차수만. 같은 (사람, 종류, 탐지건, 차수)는 한 번만
  (UNIQUE + ON CONFLICT DO NOTHING) — 순찰이 같은 기한을 다시 판정해도 쌓이지 않는다
- 업무와 같은 트랜잭션에서 만든다 — 상태가 바뀌었는데 알림만 빠지는 일이 없게
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import argus_user, detection, explanation, handler, notification, setting

DEFAULT_DUE_DAYS = 7  # 【기본값】 setting.explanation_due_days
# 웹 푸시도 보내는 (종류, 심각도) — 급한 건만 (F-1 표의 "+푸시", 발송은 webpush.dispatch_pending)
PUSH = frozenset(
    {
        ("DETECTED", "HIGH"),
        ("REQUESTED", "HIGH"),
        ("DUE_SOON", "HIGH"),
        ("DUE_SOON", "MEDIUM"),
        ("OVERDUE", "HIGH"),
        ("OVERDUE", "MEDIUM"),
    }
)
DUE_SOON_WINDOW = timedelta(hours=24)  # 남은 시간이 이보다 적으면 "기한 임박"
OPEN_REQUEST = "REQUESTED"


def due_days(conn: Connection) -> int:
    value = conn.execute(
        select(setting.c.value).where(setting.c.key == "explanation_due_days")
    ).scalar_one_or_none()
    return value if isinstance(value, int) and value > 0 else DEFAULT_DUE_DAYS


def due_at(conn: Connection):
    """소명 기한 = 지금 + 설정 일수 (DB 시각 기준 — 요청 시각 requested_at과 같은 시계)"""
    return func.now() + timedelta(days=due_days(conn))


def _officers(conn: Connection) -> list[int]:
    return list(
        conn.execute(
            select(argus_user.c.id).where(
                argus_user.c.role == "OFFICER", argus_user.c.status == "ACTIVE"
            )
        ).scalars()
    )


def _handler_user(conn: Connection, detection_id: int) -> list[int]:
    """탐지건 행위자의 A5 계정 — 탐지건 본인 판정(detections _visible)과 같은 키"""
    return list(
        conn.execute(
            select(argus_user.c.id)
            .join(handler, handler.c.id == argus_user.c.handler_id)
            .join(
                detection,
                and_(
                    detection.c.source_system_id == handler.c.source_system_id,
                    detection.c.actor_login_id == handler.c.login_id,
                ),
            )
            .where(
                detection.c.id == detection_id,
                argus_user.c.role == "HANDLER",
                argus_user.c.status != "DISABLED",
            )
        ).scalars()
    )


def notify(
    conn: Connection,
    user_ids: Iterable[int],
    kind: str,
    detection_id: int,
    severity: str,
    round_: int = 0,
) -> list[int]:
    """알림 행 추가 — 이미 같은 알림이 있으면 건너뛴다. 새로 만든 알림 id들을 돌려준다"""
    rows = [
        {
            "user_id": user_id,
            "kind": kind,
            "detection_id": detection_id,
            "severity": severity,
            "round": round_,
            "push_pending": (kind, severity) in PUSH,
        }
        for user_id in dict.fromkeys(user_ids)
    ]
    if not rows:
        return []
    return list(
        conn.execute(
            pg_insert(notification)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["user_id", "kind", "detection_id", "round"])
            .returning(notification.c.id)
        ).scalars()
    )


def on_detected(conn: Connection, detection_id: int, severity: str) -> list[int]:
    if severity != "HIGH":
        return []  # 중·하는 목록에서만 (F-1)
    return notify(conn, _officers(conn), "DETECTED", detection_id, severity)


def on_requested(conn: Connection, detection_id: int, severity: str, round_: int) -> list[int]:
    # 하 심각도라도 화면 알림 — 취급자가 해야 할 일이 생겼으므로 (F-1 【기본값】)
    return notify(
        conn, _handler_user(conn, detection_id), "REQUESTED", detection_id, severity, round_
    )


def on_submitted(
    conn: Connection, detection_id: int, severity: str, round_: int, requested_by: int | None
) -> list[int]:
    recipients = [requested_by] if requested_by is not None else _officers(conn)
    return notify(conn, recipients, "SUBMITTED", detection_id, severity, round_)


def check_due(engine: Engine, now: datetime | None = None) -> int:
    """기한 임박·초과 판정 (F-3) — 탐지 배치(worker)가 순찰마다 부른다. 만든 알림 수를 돌려준다

    대상: 아직 제출되지 않은 **현재 차수**의 소명 요청(탐지건 상태 REQUESTED).
    이미 기한을 넘긴 뒤 처음 보면 초과만 알린다(임박은 건너뜀). 상태는 바꾸지 않는다
    """
    created = 0
    with engine.begin() as conn:
        current = now or conn.execute(select(func.now())).scalar_one()
        rows = conn.execute(
            select(
                explanation.c.detection_id,
                explanation.c.round,
                explanation.c.due_at,
                detection.c.severity,
            )
            .join(
                detection,
                and_(
                    detection.c.id == explanation.c.detection_id,
                    detection.c.round == explanation.c.round,
                ),
            )
            .where(
                detection.c.status == OPEN_REQUEST,
                explanation.c.submitted_at.is_(None),
                explanation.c.due_at.is_not(None),
                explanation.c.due_at - DUE_SOON_WINDOW <= current,
            )
        ).all()
        officers = _officers(conn) if rows else []
        for detection_id, round_, due, severity in rows:
            kind = "OVERDUE" if current >= due else "DUE_SOON"
            recipients = [*_handler_user(conn, detection_id), *officers]
            created += len(notify(conn, recipients, kind, detection_id, severity, round_))
    return created
