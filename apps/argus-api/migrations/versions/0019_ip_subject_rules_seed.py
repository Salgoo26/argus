"""탐지 룰 시드 — 접속지(IP)·특정 정보주체 기반 (v0.1 보강 K-2, 갭 A10 — 안내서 95)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-10

안내서가 §8의 비정상 행위 예시로 드는 "인가되지 않은 단말·지역(IP)에서 접속", "짧은 시간 여러 IP",
"특정 정보주체 과도 조회"를 기본 룰로 둔다.

| 룰 | 경로 | 조건 | 심각도 |
| 허용 범위 밖 접속지 | 전체 | client_ip not_in_cidr [사설망 3대역 + 루프백] | 상 |
| 짧은 시간 여러 접속지 | 전체 | 1시간 윈도우 고유 접속지 수 ≥ 3 | 중 |
| 특정 회원 반복 처리 | 화면 경유 | 하루 윈도우 한 회원 최대 처리 횟수 ≥ 20 | 중 |

- 허용 범위 【기본값】은 사설망 대역(사내망 가정). 운영에서는 담당자가 룰 빌더로 회사 대역으로
  바꾼다
- 로컬 주의: 신뢰 프록시를 비운 로컬 구성에서는 화면 경유 기록의 접속지가 화면 서버 컨테이너
  주소(사설망)라 "허용 범위 밖" 룰이 걸리지 않는다 — 시연은 DB 직접 접근(게이트웨이가 받는 실제
  접속지)이나 접속지를 정한 기록으로
- 집계 룰의 조건식은 비울 수 없어 "모든 수행업무"·"개인정보 데이터"로 대상을 적는다
0008 이후라 룰 변경 이력(CREATE, 변경자 NULL = 시스템)도 함께 남긴다.
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
INSERT INTO detection_rule
    (name, description, rule_type, access_path, severity, condition, aggregate)
VALUES
(
    '허용 범위 밖 접속지',
    '허용한 접속지 대역(기본값: 사설망) 밖에서 접속·처리한 행위. 운영에서는 회사 대역으로 바꾼다',
    'EVENT',
    'ALL',
    'HIGH',
    '{"all": [{"field": "client_ip", "op": "not_in_cidr",
                "value": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]}]}',
    NULL
),
(
    '짧은 시간 여러 접속지',
    '한 시간(매시 정각부터, 한국 시각) 안에 서로 다른 접속지 3곳 이상에서 접속·처리한 행위',
    'AGGREGATE',
    'ALL',
    'MEDIUM',
    '{"all": [{"field": "action", "op": "in",
                "value": ["LOGIN", "LOGOUT", "READ", "CREATE", "UPDATE", "DELETE",
                          "DOWNLOAD"]}]}',
    '{"window": "1h", "measure": "DISTINCT_IP", "compare": "ABSOLUTE", "threshold": 3}'
),
(
    '특정 회원 반복 처리',
    '하루(0시부터, 한국 시각) 동안 같은 회원의 개인정보를 20번 이상 처리한 행위.'
    || ' 회원번호가 있는 기록만 센다',
    'AGGREGATE',
    'APP',
    'MEDIUM',
    '{"all": [{"field": "data_category", "op": "in",
                "value": ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"]}]}',
    '{"window": "1d", "measure": "MAX_SUBJECT_REPEAT", "compare": "ABSOLUTE", "threshold": 20}'
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
 WHERE created_by IS NULL
   AND name IN ('허용 범위 밖 접속지', '짧은 시간 여러 접속지', '특정 회원 반복 처리');
"""

DOWNGRADE_SQL = """
DELETE FROM detection_rule_history
 WHERE rule_id IN (
       SELECT id FROM detection_rule
        WHERE created_by IS NULL
          AND name IN ('허용 범위 밖 접속지', '짧은 시간 여러 접속지', '특정 회원 반복 처리'));
DELETE FROM detection_rule
 WHERE created_by IS NULL
   AND name IN ('허용 범위 밖 접속지', '짧은 시간 여러 접속지', '특정 회원 반복 처리');
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
