"""소명 근거자료 첨부 (기능 레이어 7 ③, LOG-07, 결정 10)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-02

db-schema 3-4의 explanation_attachment DDL + 인덱스. 파일 자체는 DB가 아니라 Argus 전용 볼륨에
두고(stored_path는 서버가 만든 이름 — 사용자가 보낸 파일 이름은 쓰지 않는다), DB에는 메타데이터와
SHA-256만 둔다. 내려받을 때마다 SHA-256을 다시 계산해 변조를 확인한다.

권한: 앱 계정은 조회·추가·삭제. 삭제는 애플리케이션이 **제출 전**(REQUESTED)에만 허용한다 —
잘못 올린 파일을 고칠 수 있게. 제출 뒤에는 근거가 바뀌지 않는다.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE explanation_attachment (
    id               bigserial    PRIMARY KEY,
    explanation_id   bigint       NOT NULL REFERENCES explanation(id),
    original_name    varchar(255) NOT NULL,     -- 표시용 (정리한 이름) — 저장 경로에는 쓰지 않음
    stored_path      varchar(500) NOT NULL,     -- 로컬 볼륨 경로 (1차)
    content_type     varchar(100) NOT NULL,     -- 파일 내용(매직 바이트)으로 판정한 형식
    size_bytes       bigint       NOT NULL,
    sha256           char(64)     NOT NULL,     -- 제출 후 변조 여부 확인
    uploaded_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_explanation_attachment ON explanation_attachment (explanation_id);

GRANT SELECT, INSERT, DELETE ON explanation_attachment TO argus_app;
GRANT USAGE, SELECT ON SEQUENCE explanation_attachment_id_seq TO argus_app;
"""

DOWNGRADE_SQL = """
DROP TABLE explanation_attachment;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
