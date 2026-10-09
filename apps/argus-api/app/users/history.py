"""Argus 계정 이력 기록 (v0.1 보강 L-4) — 화면·운영 스크립트·명부 동기화가 함께 쓴다

계정을 바꾸는 곳은 모두 이 함수로 이력을 남긴다 — 같은 트랜잭션이라 변경과 이력이 함께 커밋된다.
"""

from typing import Any

from sqlalchemy import Connection, insert

from app.models import argus_user_history


def record_user_change(
    conn: Connection,
    *,
    user: Any,
    change_type: str,
    after_role: str,
    after_status: str,
    reason: str,
    before: Any | None = None,
    actor: Any | None = None,
) -> None:
    """user·before: argus_user 행(id·login_id·role·status), actor: 처리한 담당자(None = 시스템)"""
    conn.execute(
        insert(argus_user_history).values(
            user_id=user.id,
            login_id=user.login_id,
            change_type=change_type,
            before_role=before.role if before is not None else None,
            after_role=after_role,
            before_status=before.status if before is not None else None,
            after_status=after_status,
            reason=reason,
            actor_user_id=actor.id if actor is not None else None,
            actor_login_id=actor.login_id if actor is not None else None,
        )
    )
