"""요청 하나의 "기록지" — contextvars (Spring의 ThreadLocal 자리)

동기(def) 핸들러·의존성은 스레드풀에서 컨텍스트 **복사본**으로 실행되므로 ContextVar.set()은
미들웨어에 보이지 않는다. 미들웨어가 넣어 둔 AccessRecord 객체의 **내용만** 채운다.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime


@dataclass
class AccessRecord:
    occurred_at: datetime
    client_ip: str
    method: str
    query_keys: list[str]
    actor_login_id: str | None = None
    # 처리한 정보주체 "건수"만 — Argus 자체 기록은 회원 PK를 다시 적재하지 않는다 (policy 6-3)
    subject_count: int = 0


_current: ContextVar[AccessRecord | None] = ContextVar("argus_access_record", default=None)


def start_record(record: AccessRecord):
    return _current.set(record)


def end_record(token) -> None:
    _current.reset(token)


def record_actor(login_id: str) -> None:
    record = _current.get()
    if record is not None:
        record.actor_login_id = login_id


def record_subject_count(count: int) -> None:
    """화면에 보여준(마스킹된) 정보주체 식별값의 수"""
    record = _current.get()
    if record is not None:
        record.subject_count = count
