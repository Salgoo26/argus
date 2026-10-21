"""회원가입 변경 — 별도 아이디·이메일 인증·휴대폰 본인확인 결과 (커머스 확장, 판단기록 F1·F2)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21

- login_id: 이메일과 별개인 로그인 아이디(영문 소문자로 시작, 영문 소문자·숫자 4~20자, 유일)
- email_verified_at: 이메일 인증 시각 (데모라 인증 버튼을 누르면 바로 인증된다)
- 본인확인 결과 (F1 — 받은 값을 모두 저장. 최소수집 검토는 이후 영향평가에서):
  ci(연계정보, 유일 — 1인 1계정)·di(중복가입확인정보)·birth_date·gender·is_foreigner·carrier·
  identity_verified_at. 이름·휴대폰은 기존 name·phone 칸에 본인확인 결과로 들어간다.
  주민등록번호(앞 7자리 포함)는 칸이 없다 — 가상 본인확인 창 안에서만 입력되고 서버로 오지 않는다
  (개인정보 보호법 제24조의2)
- 기존 회원: 시드 회원(userNNNN@example.com)은 아이디 userNNNN, 나머지는 member{번호}.
  본인확인·이메일 인증 값은 비워 둔다(지어 넣지 않는다 — 시드의 가상 값은 seed.py backfill_identity)
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE member
    ADD COLUMN login_id              varchar(20),
    ADD COLUMN email_verified_at     timestamptz,
    ADD COLUMN ci                    varchar(88),
    ADD COLUMN di                    varchar(64),
    ADD COLUMN birth_date            date,
    ADD COLUMN gender                char(1) CHECK (gender IN ('M', 'F')),
    ADD COLUMN is_foreigner          boolean,
    ADD COLUMN carrier               varchar(16),
    ADD COLUMN identity_verified_at  timestamptz;

UPDATE member
SET login_id = CASE
    WHEN email ~ '^user[0-9]{4}@example\.com$' THEN split_part(email, '@', 1)
    ELSE 'member' || id
END;

ALTER TABLE member
    ALTER COLUMN login_id SET NOT NULL,
    ADD CONSTRAINT member_login_id_key UNIQUE (login_id),
    ADD CONSTRAINT member_ci_key UNIQUE (ci);
"""

DOWNGRADE_SQL = """
ALTER TABLE member
    DROP CONSTRAINT member_ci_key,
    DROP CONSTRAINT member_login_id_key,
    DROP COLUMN identity_verified_at,
    DROP COLUMN carrier,
    DROP COLUMN is_foreigner,
    DROP COLUMN gender,
    DROP COLUMN birth_date,
    DROP COLUMN di,
    DROP COLUMN ci,
    DROP COLUMN email_verified_at,
    DROP COLUMN login_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
