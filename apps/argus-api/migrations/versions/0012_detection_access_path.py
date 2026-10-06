"""탐지건의 접근 경로 + DB 직접 접근(2티어) 기본 룰 (기능 레이어 8 구현 순서 ③)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-06

1. **탐지건 하나 = 경로 하나** (policy 1-5, db-schema v0.7): `detection.access_path`를 두고, 진행 중
   탐지건의 묶음 기준에 경로를 넣는다. 적용 경로가 "전체"인 룰도 화면 경유(APP)·DB 직접(DB) 기록을
   한 탐지건에 섞지 않는다 — 두 경로는 성격이 다른 기록이고 보고도 따로 하기 때문(사용자 실무 경험).
   기존 탐지건은 모두 화면 경유 기록뿐이라 APP으로 채운다.
2. **DB 직접 접근 기본 룰 3개** (2026-10-06 사용자 결정 — policy 1-3 "2단계 룰" 5개 대신):
   야간·주말·전월 대비 급증만 기본으로 두고, 나머지는 담당자가 룰 빌더에서 만든다.
   - 3티어 같은 이름 룰보다 심각도를 한 단계 올렸다 — DB 직접 접근은 평소 드문 경로라
     같은 시간대 접근이라도 위험이 크다
   - 조건은 개인정보 데이터(회원·주문·문의·결제수단)를 처리한 문장으로 한정
     — DB 툴의 접속(LOGIN)이나
     업무 외 테이블 조회까지 잡으면 접속 한 번에 탐지가 여러 건 생긴다
   - 퇴직자 계정 룰은 화면 경유에만 둔다(사용자 결정 — DB 경로로 넓히지 않음)
시드 룰 이름은 모두 "DB 직접 "으로 시작한다(되돌릴 때 이 접두어로 찾는다).
0008 이후라 룰 변경 이력(CREATE, 변경자 NULL = 시스템)도 함께 남긴다.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE detection
    ADD COLUMN access_path varchar(8) NOT NULL DEFAULT 'APP' CHECK (access_path IN ('APP','DB'));

DROP INDEX ux_detection_open_group;
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, access_path, actor_login_id, group_bucket)
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');

INSERT INTO detection_rule
    (name, description, rule_type, access_path, severity, condition, aggregate)
VALUES
(
    'DB 직접 야간 접근',
    '업무 시간 밖(22:00~06:00, 한국 시각)에 DB 툴로 개인정보를 처리한 행위 (policy 1-3)',
    'EVENT',
    'DB',
    'HIGH',
    '{"all": [{"field": "data_category", "op": "in",
               "value": ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"]},
              {"field": "occurred_time", "op": "between", "value": ["22:00", "06:00"]}]}',
    NULL
),
(
    'DB 직접 주말 접근',
    '토·일요일(한국 시각)에 DB 툴로 개인정보를 처리한 행위 (policy 1-3)',
    'EVENT',
    'DB',
    'MEDIUM',
    '{"all": [{"field": "data_category", "op": "in",
               "value": ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"]},
              {"field": "occurred_weekday", "op": "in", "value": ["SAT", "SUN"]}]}',
    NULL
),
(
    'DB 직접 전월 대비 급증',
    '이달 1일부터 지금까지 DB 툴로 개인정보를 처리한 문장 수가 지난달 같은 기간의 2배 이상.'
    || ' 지난달 같은 기간이 20건 미만이면(첫 달·월초 등) 비율을 믿을 수 없어 판정하지 않는다'
    || ' (policy 1-3)',
    'AGGREGATE',
    'DB',
    'MEDIUM',
    '{"all": [{"field": "data_category", "op": "in",
               "value": ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"]}]}',
    '{"window": "1mo", "measure": "LOG_COUNT", "compare": "RATIO_TO_BASELINE",
      "baseline": "PREV_MONTH_SAME_PERIOD", "threshold": 2.0, "min_baseline": 20}'
);

INSERT INTO detection_rule_history (rule_id, version, change_type, snapshot, changed_by, changed_at)
SELECT id, version, 'CREATE',
       jsonb_build_object(
           'id', id, 'name', name, 'description', description, 'rule_type', rule_type,
           'access_path', access_path, 'severity', severity, 'enabled', enabled,
           'auto_request', auto_request, 'condition', condition, 'aggregate', aggregate,
           'group_by', group_by, 'version', version),
       NULL, created_at
  FROM detection_rule
 WHERE name LIKE 'DB 직접 %' AND created_by IS NULL;
"""

DOWNGRADE_SQL = """
DELETE FROM detection_rule_history
 WHERE rule_id IN (SELECT id FROM detection_rule
                    WHERE name LIKE 'DB 직접 %' AND created_by IS NULL);
DELETE FROM detection_rule WHERE name LIKE 'DB 직접 %' AND created_by IS NULL;

DROP INDEX ux_detection_open_group;
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, actor_login_id, group_bucket)
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');
ALTER TABLE detection DROP COLUMN access_path;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
