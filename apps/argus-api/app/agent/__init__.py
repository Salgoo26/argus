"""Argus 자체 접속기록 Agent (LOG-17, api-spec 2-7) — 감시하는 쪽의 행위도 같은 원장에 남긴다

플랫폼 Agent와 같은 구조(문패 + 미들웨어 + 기록지)지만 두 가지가 다르다.
- outbox·HTTP를 거치지 않고 **같은 append 함수로 원장에 직접** 기록한다
  (자기 자신에게 HTTP를 보내지 않음)
- 정보주체는 **건수만** 남긴다 — 조회할 때마다 회원 PK를 원장에 다시 쌓지 않게 (policy 6-3 최소처리)

두 시스템은 별개 제품이라 플랫폼 Agent 코드를 공유하지 않는다.
"""

from app.agent.context import (
    record_actor,
    record_query_keys,
    record_report,
    record_subject_count,
    record_target,
)
from app.agent.decorators import access_log, access_log_exempt

__all__ = [
    "access_log",
    "access_log_exempt",
    "record_actor",
    "record_query_keys",
    "record_report",
    "record_subject_count",
    "record_target",
]
