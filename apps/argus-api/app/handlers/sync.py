"""취급자 동기화 적용 (api-spec 3-1 "멱등·중복 판정", v0.3)

이벤트가 변경분이 아니라 취급자 상태 전체(스냅샷)를 싣고 오므로, event_id를 저장하지 않고
last_event_at 비교만으로 멱등이 성립한다.

- occurred_at > last_event_at → 상태를 덮어쓰고 accepted
- 그 외(재전송, 늦게 도착한 옛 이벤트) → 아무것도 바꾸지 않고 duplicates
- 비교와 쓰기를 INSERT ... ON CONFLICT DO UPDATE ... WHERE 한 문장으로 처리한다.
  조회 후 저장하면 그 사이에 다른 요청이 끼어들 수 있다
  (JPA의 "조회 → 비교 → save"에 락을 거는 대신 DB가 한 번에 판정)
"""

import logging
from collections.abc import Iterable

from sqlalchemy import Connection, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.auth.passwords import unusable_password_hash
from app.ingest.handler_validation import HandlerState
from app.models import argus_user, handler, push_subscription

logger = logging.getLogger(__name__)


def apply_handler_events(
    conn: Connection, source_system_id: int, states: Iterable[HandlerState]
) -> tuple[int, int]:
    """받은 순서대로 적용하고 (accepted, duplicates)를 돌려준다.

    같은 배치에 같은 취급자의 이벤트가 순서가 뒤바뀌어 들어와도 건마다 last_event_at과
    비교하므로 결과는 occurred_at이 가장 늦은 상태가 된다.
    """
    accepted = duplicates = 0
    for state in states:
        handler_id = _upsert_if_newer(conn, source_system_id, state)
        if handler_id is None:
            duplicates += 1
            continue
        accepted += 1
        _sync_a5_account(conn, handler_id, state)
    return accepted, duplicates


def _upsert_if_newer(conn: Connection, source_system_id: int, state: HandlerState) -> int | None:
    stmt = insert(handler).values(
        source_system_id=source_system_id,
        login_id=state.login_id,
        name=state.name,
        team=state.team,
        employment_status=state.employment_status,
        terminated_at=state.terminated_at,
        last_event_at=state.occurred_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[handler.c.source_system_id, handler.c.login_id],
        set_={
            "name": stmt.excluded.name,
            "team": stmt.excluded.team,
            "employment_status": stmt.excluded.employment_status,
            "terminated_at": stmt.excluded.terminated_at,
            "last_event_at": stmt.excluded.last_event_at,
            "updated_at": func.now(),
        },
        # 순서 역전 방지 — 이 조건이 거짓이면 아무 행도 바뀌지 않고 RETURNING도 비어 있다
        where=handler.c.last_event_at < stmt.excluded.last_event_at,
    )
    return conn.execute(stmt.returning(handler.c.id)).scalar_one_or_none()


def _sync_a5_account(conn: Connection, handler_id: int, state: HandlerState) -> None:
    """A5(취급자) Argus 계정 — 여러 번 적용돼도 결과가 같게 "상태 기준"으로 (api-spec 3-1 #3)"""
    if state.employment_status == "TERMINATED":
        # 퇴직 → 로그인 차단. 진행 중인 소명 건은 담당자가 판단한다(DISMISS·ESCALATE)
        conn.execute(
            update(argus_user)
            .where(argus_user.c.handler_id == handler_id, argus_user.c.status != "DISABLED")
            .values(status="DISABLED")
        )
        # 막힌 계정의 웹 푸시 구독도 지운다 (v0.1 보강 F-4)
        conn.execute(
            delete(push_subscription).where(
                push_subscription.c.user_id.in_(
                    select(argus_user.c.id).where(argus_user.c.handler_id == handler_id)
                )
            )
        )
        return

    # 재직 → 계정이 없을 때만 만든다. 이미 있으면(DISABLED 포함) 건드리지 않는다:
    # 재입사로 ACTIVE가 와도 막힌 계정을 자동으로 되살리지 않는다 — 권한 복구는 사람이 판단
    linked = select(argus_user.c.id).where(argus_user.c.handler_id == handler_id)
    if conn.execute(linked).first() is not None:
        return

    created = conn.execute(
        insert(argus_user)
        .values(
            login_id=state.login_id,
            password_hash=unusable_password_hash(),
            role="HANDLER",
            handler_id=handler_id,
        )
        .on_conflict_do_nothing(index_elements=[argus_user.c.login_id])
        .returning(argus_user.c.id)
    ).scalar_one_or_none()
    if created is None and conn.execute(linked).first() is None:
        # 같은 login_id를 다른 Argus 계정(예: 정보보호 담당자)이 이미 쓰고 있다.
        # 남의 계정을 취급자 계정으로 바꿔치기하지 않고, 사람이 정리하도록 남긴다
        logger.warning(
            "A5 account not created: login_id already used by another Argus account "
            "(handler_id=%s)",
            handler_id,
        )
