"""소명에 관련 업무 티켓 번호 (기능 레이어 7 후속, 2026-10-02 사용자 결정)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-02

취급자가 소명 내용과 **별도 칸**에 관련 1:1 문의 티켓 번호(INQ-12)를 직접 입력한다(최대 3개).
Argus는 티켓 내용(고객이 쓴 글)을 갖지 않는다 — 절대 규칙 #3. 담당자는 탐지건 상세의 링크로
플랫폼 관리자 화면에 넘어가 내용을 확인한다(그 열람도 플랫폼 접속기록으로 Argus에 남는다).
번호의 검증은 취급자와 담당자가 한다(형식만 서버가 검사).

제출 때 함께 저장되고, 제출 뒤에는 바뀌지 않는다(제출 이후 소명 행을 고치는 API가 없다).
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE explanation
    ADD COLUMN ticket_ids varchar(32)[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(ticket_ids) <= 3);
"""

DOWNGRADE_SQL = """
ALTER TABLE explanation DROP COLUMN ticket_ids;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
