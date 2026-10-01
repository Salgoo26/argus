"""접속기록 Agent — 상용 솔루션의 WAS Agent를 FastAPI로 재현 (architecture 3절)

| 상용 (Java/WAS)      | 여기                                   |
|---------------------|---------------------------------------|
| Servlet Filter      | middleware.AccessLogMiddleware (ASGI) |
| ThreadLocal         | context (contextvars)                 |
| URL → 업무 매핑 설정 | decorators.access_log                 |

핸들러가 쓰는 것은 아래 셋뿐이다.
"""

from app.agent.context import record_actor, record_context, record_subjects
from app.agent.decorators import access_log, access_log_exempt

__all__ = [
    "access_log",
    "access_log_exempt",
    "record_actor",
    "record_context",
    "record_subjects",
]
