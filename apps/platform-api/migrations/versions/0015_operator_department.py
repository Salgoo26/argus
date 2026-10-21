"""관리자 소속을 부서 3개로 (PIA 판단기록 P1~P6)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-22

- operator.team: OPS·CS·MARKETING → COMMERCE_OPS(커머스운영팀)·MARKETING(마케팅팀)·IT(IT팀).
  운영과 CS는 업무가 겹쳐 한 팀으로 합치고, 계정·권한·DB 직접접근·정보보호 업무는 IT팀이 맡는다
- 기존 계정: 역할 ADMIN → IT, 마케팅 → 그대로, 나머지(OPS·CS) → COMMERCE_OPS.
  역할(role)과 권한은 바꾸지 않는다 — 접근 범위는 역할로만 정한다(policy 4-5)
- 바뀐 계정마다 권한 이력 CHANGE 1행(처리자 시스템 = NULL, §5③ 변경 기록)과
  취급자 동기화 이벤트(HANDLER_UPDATED)를 outbox에 넣는다 — Argus 명부도 평소 경로로 바뀐다.
  이력의 옛 행은 그 시점 값이라 고치지 않는다(append-only)
"""

import json
import uuid
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

REASON = "조직 개편 — 소속을 부서 3개(커머스운영팀·마케팅팀·IT팀)로 통합"


def _handler_event(row, occurred_at: datetime) -> dict:
    # app.outbox.handler_event와 같은 형식(api-spec 3-1) — 마이그레이션은 앱 코드를 불러오지 않는다
    handler = {
        "login_id": row.login_id,
        "name": row.name,
        "team": row.team,
        "employment_status": row.employment_status,
    }
    if row.terminated_at is not None:
        handler["terminated_at"] = row.terminated_at.isoformat(timespec="microseconds")
    return {
        "event_id": str(uuid.uuid4()),
        "type": "HANDLER_UPDATED",
        "occurred_at": occurred_at.isoformat(timespec="microseconds"),
        "handler": handler,
    }


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE operator DROP CONSTRAINT operator_team_check"))
    changed = conn.execute(
        text("""
            WITH target AS (
                SELECT id, team AS before_team,
                       CASE WHEN role = 'ADMIN' THEN 'IT'
                            WHEN team = 'MARKETING' THEN 'MARKETING'
                            ELSE 'COMMERCE_OPS' END AS new_team
                FROM operator)
            UPDATE operator o SET team = t.new_team, updated_at = now()
            FROM target t WHERE t.id = o.id AND t.before_team <> t.new_team
            RETURNING o.id, o.login_id, o.name, o.team, o.role, o.employment_status,
                      o.terminated_at, t.before_team
        """)
    ).all()
    conn.execute(
        text("""ALTER TABLE operator ADD CONSTRAINT operator_team_check
                CHECK (team IN ('COMMERCE_OPS','MARKETING','IT'))""")
    )
    for row in changed:
        conn.execute(
            text("""
                INSERT INTO operator_permission_history
                    (operator_id, change_type, before_role, after_role, before_team, after_team,
                     before_status, after_status, reason)
                VALUES (:id, 'CHANGE', :role, :role, :before_team, :team, :status, :status, :reason)
            """),
            {
                "id": row.id,
                "role": row.role,
                "before_team": row.before_team,
                "team": row.team,
                "status": row.employment_status,
                "reason": REASON,
            },
        )
        event = _handler_event(row, datetime.now(UTC))
        conn.execute(
            text("""INSERT INTO outbox (event_id, topic, payload)
                    VALUES (:event_id, 'HANDLER', CAST(:payload AS jsonb))"""),
            {"event_id": event["event_id"], "payload": json.dumps(event, ensure_ascii=False)},
        )


def downgrade() -> None:
    # 커머스운영팀·IT팀은 옛 OPS로 되돌린다(CS였는지는 알 수 없다). 이력·전송된 이벤트는 남는다
    op.execute("""
ALTER TABLE operator DROP CONSTRAINT operator_team_check;
UPDATE operator SET team = 'OPS' WHERE team IN ('COMMERCE_OPS', 'IT');
ALTER TABLE operator ADD CONSTRAINT operator_team_check
    CHECK (team IN ('CS','MARKETING','OPS'));
""")
