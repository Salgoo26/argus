"""화면 알림 + 소명 기한 (v0.1 보강 F-1~F-3 — 안내서 129쪽 "이상행위 탐지 시 알림")

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-10

- notification: 받는 사람별 알림 한 줄. **본문 없음** — 종류·탐지건 번호·심각도·차수만 저장하고
  화면은 탐지건의 룰 이름을 붙여 보여 준다(개인정보 없음). 같은 (사람, 종류, 탐지건, 차수)는 한 번만
  — 탐지 배치가 기한 임박·초과를 순찰마다 다시 판정해도 알림이 쌓이지 않게
- explanation.due_at: 소명 기한 = 요청 시각 + setting.explanation_due_days(기본 7일).
  재요청은 다시 7일. 기한을 넘겨도 상태는 바뀌지 않는다(담당자 판단).
  이미 있는 요청은 요청 시각 + 7일로 채운다
- 앱 계정: 알림은 조회·추가·읽음 표시(UPDATE)만, 삭제 없음. 알림은 파생 데이터라 FK는 ON DELETE
  CASCADE — 운영에서는 탐지건·계정을 지우지 않으므로 사실상 테스트 정리용
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE explanation ADD COLUMN due_at timestamptz;
UPDATE explanation SET due_at = requested_at + interval '7 days' WHERE due_at IS NULL;

INSERT INTO setting (key, value) VALUES ('explanation_due_days', '7');

CREATE TABLE notification (
    id            bigserial   PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES argus_user(id) ON DELETE CASCADE,
    kind          varchar(16) NOT NULL
                  CHECK (kind IN ('DETECTED','REQUESTED','DUE_SOON','OVERDUE','SUBMITTED')),
    detection_id  bigint      NOT NULL REFERENCES detection(id) ON DELETE CASCADE,
    severity      varchar(8)  NOT NULL CHECK (severity IN ('HIGH','MEDIUM','LOW')),
    round         int         NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    read_at       timestamptz,
    UNIQUE (user_id, kind, detection_id, round)
);
CREATE INDEX ix_notification_user ON notification (user_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE ON notification TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE notification_id_seq TO argus_app;
"""

DOWNGRADE_SQL = """
DROP TABLE notification;
DELETE FROM setting WHERE key = 'explanation_due_days';
ALTER TABLE explanation DROP COLUMN due_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
