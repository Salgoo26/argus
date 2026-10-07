"""개인정보 수집·이용 동의(필수) 문안 v2 — 가입 항목 변경 (기능 레이어 7-4 ①, policy 4-3)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07

휴대폰이 가입 필수가 되고 주소가 배송지로 옮겨 가므로, 동의받을 때 알리는 수집 항목(§15②2호)도
바꾼다. 문안을 고쳐 쓰지 않고 **버전을 올린다** — member_consent.item_version에 남은 v1 동의는
"그때 알린 항목"의 증적이라, 같은 버전의 문안이 바뀌면 증적이 어긋난다.
문안은 여전히 자리표시(Cowork 기획 방에서 정식 문안 작성 예정).
기존 회원의 v2 재동의 절차는 만들지 않는다 — 구현 로그 "보안성 검토 이월".
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
UPDATE consent_item
SET version = 'v2',
    purpose = '회원 식별·가입 의사 확인, 주문·결제·환불 처리, 주문 상품 배송·배송 연락, '
              '1:1 문의 응대',
    items = '이메일, 비밀번호, 이름, 휴대전화번호 / (마이페이지에서 등록 시) '
            '배송지(받는 사람·연락처·주소), 환불계좌',
    effective_from = '2026-10-07T00:00:00+09:00'
WHERE code = 'PRIVACY_REQUIRED';
"""

DOWNGRADE_SQL = """
UPDATE consent_item
SET version = 'v1',
    purpose = '회원 식별·가입 의사 확인, 주문·결제·환불 처리, 1:1 문의 응대',
    items = '이메일, 비밀번호, 이름 / (마이페이지에서 입력 시) 휴대전화번호, 주소, 환불계좌',
    effective_from = '2026-10-01T00:00:00+09:00'
WHERE code = 'PRIVACY_REQUIRED';
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
