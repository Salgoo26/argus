"""고객 화면 최소판 — 동의 항목·동의 이력, 고객 로그인 실패 잠금 (기능 레이어 7 결정 2·5)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02

- consent_item·member_consent: db-schema 4절 DDL 그대로
- member에 로그인 실패 횟수·잠금 해제 시각 (설계 변경 — 5회 실패 → 15분 자동 해제)
- 동의 항목 4개 시드. 문안(목적·항목·기간)이 바뀌면 행을 고쳐 쓰지 않고 버전을 올린다
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
-- 고객 로그인 실패 제한 — 비밀번호 찾기가 없어 영구 잠금 대신 시간 경과로 자동 해제
ALTER TABLE member
    ADD COLUMN failed_login_count int         NOT NULL DEFAULT 0,
    ADD COLUMN locked_until       timestamptz;

-- 동의 항목 정의 (PLT-01)
CREATE TABLE consent_item (
    code            varchar(32)  PRIMARY KEY,   -- 'TOS','PRIVACY_REQUIRED','MARKETING'
    name            varchar(100) NOT NULL,
    required        boolean      NOT NULL,      -- 필수/선택 (PIPA §22①·⑤)
    version         varchar(16)  NOT NULL,
    purpose         text         NOT NULL,      -- 수집·이용 목적 (§15②1호)
    items           text         NOT NULL,      -- 수집 항목 (§15②2호)
    retention       text         NOT NULL,      -- 보유·이용 기간 (§15②3호)
    effective_from  timestamptz  NOT NULL
);

-- 동의·철회 이력 (append) — PLT-01
CREATE TABLE member_consent (
    id            bigserial   PRIMARY KEY,
    member_id     bigint      NOT NULL REFERENCES member(id),
    item_code     varchar(32) NOT NULL REFERENCES consent_item(code),
    item_version  varchar(16) NOT NULL,          -- 동의 당시 약관 버전
    agreed        boolean     NOT NULL,          -- true=동의, false=미동의/철회
    acted_at      timestamptz NOT NULL DEFAULT now(),
    client_ip     inet,                          -- 동의 증적의 신뢰성 확보용
    method        varchar(16) NOT NULL DEFAULT 'WEB_FORM' CHECK (method IN ('WEB_FORM'))
);
CREATE INDEX ix_member_consent ON member_consent (member_id, item_code, acted_at DESC);

INSERT INTO consent_item (code, name, required, version, purpose, items, retention, effective_from)
VALUES
  ('TOS', '이용약관 동의', true, 'v1',
   '서비스 이용 계약의 체결',
   '— (이용약관 전문은 이용약관 페이지)',
   '회원 탈퇴 시까지',
   '2026-10-01T00:00:00+09:00'),
  ('PRIVACY_REQUIRED', '개인정보 수집·이용 동의 (필수)', true, 'v1',
   '회원 식별·가입 의사 확인, 주문·결제·환불 처리, 1:1 문의 응대',
   '이메일, 비밀번호, 이름 / (마이페이지에서 입력 시) 휴대전화번호, 주소, 환불계좌',
   '회원 탈퇴 시 즉시 파기. 단, 관계 법령에 따라 보존할 기록은 해당 기간 분리 보관',
   '2026-10-01T00:00:00+09:00'),
  ('AGE_OVER_14', '만 14세 이상 확인', true, 'v1',
   '만 14세 미만 아동의 개인정보 처리 시 법정대리인 동의가 필요하므로 연령 확인 '
   '(개인정보 보호법 §22의2)',
   '만 14세 이상 여부',
   '회원 탈퇴 시까지',
   '2026-10-01T00:00:00+09:00'),
  ('MARKETING', '마케팅 정보 수신 동의 (선택)', false, 'v1',
   '이벤트·혜택 안내 (이메일)',
   '이메일, 이름',
   '동의 철회 또는 회원 탈퇴 시까지',
   '2026-10-01T00:00:00+09:00');
"""

DOWNGRADE_SQL = """
DROP TABLE member_consent;
DROP TABLE consent_item;
ALTER TABLE member DROP COLUMN locked_until, DROP COLUMN failed_login_count;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
