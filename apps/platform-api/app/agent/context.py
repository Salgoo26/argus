"""요청 하나의 "기록지" — contextvars에 담는다 (Spring의 ThreadLocal 자리)

비동기 서버는 스레드 하나가 여러 요청을 번갈아 처리하므로 ThreadLocal로는 요청끼리 값이 섞인다.
contextvars는 요청(작업) 단위로 값을 분리한다.

주의 — 동기(def) 핸들러·의존성은 FastAPI가 스레드풀에서 **컨텍스트 복사본**으로 실행한다.
거기서 ContextVar.set()을 하면 복사본만 바뀌어 미들웨어가 보지 못한다.
그래서 미들웨어가 AccessRecord 객체 하나를 넣어 두고, 모두 그 객체의 내용만 채운다
(복사본도 같은 객체를 가리키므로 채운 내용이 미들웨어에 보인다).
아래 record_* 함수가 이 규칙을 지킨다.
"""

from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime


@dataclass
class AccessRecord:
    # 미들웨어가 요청 시작 시 채움 — §2 3호 접속일시·접속지, 수행업무 재구성 근거
    occurred_at: datetime
    client_ip: str
    method: str
    query_keys: list[str]
    # 인증 의존성(또는 로그인 핸들러)이 채움 — §2 3호 식별자
    actor_login_id: str | None = None
    # 핸들러가 채움 — §2 3호 처리한 정보주체 (회원 내부 PK만)
    subject_ids: list[str] | None = None
    subject_count: int | None = None
    # 핸들러가 채움 — api-spec 2-3 context (정의된 키만, 예: 문의 처리의 ticket_id)
    context: dict | None = None


_current: ContextVar[AccessRecord | None] = ContextVar("access_record", default=None)


def start_record(record: AccessRecord):
    """미들웨어 전용. 돌려받은 토큰으로 요청 끝에 reset한다."""
    return _current.set(record)


def end_record(token) -> None:
    _current.reset(token)


def record_actor(login_id: str) -> None:
    """행위한 취급자 계정. 기록 중인 요청이 아니면(고객 라우트·배치 등) 아무것도 하지 않는다."""
    record = _current.get()
    if record is not None:
        record.actor_login_id = login_id


def record_subjects(ids: Iterable[int | str], count: int | None = None) -> None:
    """처리한 정보주체 — 회원 내부 PK만 넘긴다. 이름·이메일 등 원본 정보 금지 (CLAUDE.md 3절 #3)

    대상이 정해진 요청(회원 한 명 수정 등)은 업무 로직보다 **먼저** 부른다. 그래야 업무가 실패해도
    "누구의 정보를 처리하려 했는지"가 FAILURE 기록에 남는다.
    count는 목록·다운로드처럼 ids가 잘릴 수 있을 때의 전체 건수(생략 시 ids 개수).
    """
    record = _current.get()
    if record is None:
        return
    record.subject_ids = [str(i) for i in ids]
    record.subject_count = len(record.subject_ids) if count is None else count


def record_query_keys(keys: Iterable[str]) -> None:
    """검색 조건의 **키 이름** — 본문(POST)으로 받은 검색용 (v0.1 보강 E). 값은 넘기지 않는다.
    URL 쿼리의 키 이름은 미들웨어가 이미 적어 두며, 이 함수가 그것을 대신한다."""
    record = _current.get()
    if record is not None:
        record.query_keys = sorted(set(keys))


CONTEXT_KEYS = frozenset({"ticket_id"})  # 플랫폼이 쓰는 api-spec 2-3 context 키


def record_context(**values: str) -> None:
    """부가 정보 — 업무 근거(티켓 ID 등). 개인정보(이름·문의 내용 등)는 넣지 않는다
    (CLAUDE.md 3절 #3).

    Argus는 정의되지 않은 키를 이벤트째 거부한다(UNKNOWN_CONTEXT_KEY) — 여기서 먼저 막는다.
    """
    unknown = set(values) - CONTEXT_KEYS
    if unknown:
        raise ValueError(f"unknown access log context key: {sorted(unknown)}")
    record = _current.get()
    if record is None:
        return
    record.context = {**(record.context or {}), **values}
