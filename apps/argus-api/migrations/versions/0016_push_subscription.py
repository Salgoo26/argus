"""웹 푸시 구독 (v0.1 보강 F-4 — 화면 알림의 추가 수단)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-10

- push_subscription: 사용자가 브라우저에서 "알림 받기"를 허락했을 때 브라우저가 준 구독 정보
  (푸시 서비스 주소·암호화 공개키·인증 비밀). 로그아웃·계정 비활성화·푸시 서비스가 410을 주면 지운다
- notification.push_pending: 이 알림을 웹 푸시로도 보내야 하는가
  (심각도별 표 — app/notifications/events.py). 발송(성공·실패와 관계없이 한 번 시도)하면 false
  — 화면 알림이 주 수단이고 푸시는 보조라 재시도하지 않는다
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE push_subscription (
    id          bigserial     PRIMARY KEY,
    user_id     bigint        NOT NULL REFERENCES argus_user(id) ON DELETE CASCADE,
    endpoint    varchar(1000) NOT NULL UNIQUE,
    p256dh      varchar(128)  NOT NULL,
    auth        varchar(64)   NOT NULL,
    created_at  timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX ix_push_subscription_user ON push_subscription (user_id);

ALTER TABLE notification ADD COLUMN push_pending boolean NOT NULL DEFAULT false;
CREATE INDEX ix_notification_push_pending ON notification (id) WHERE push_pending;

GRANT SELECT, INSERT, UPDATE, DELETE ON push_subscription TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE push_subscription_id_seq TO argus_app;
"""

DOWNGRADE_SQL = """
ALTER TABLE notification DROP COLUMN push_pending;
DROP TABLE push_subscription;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
