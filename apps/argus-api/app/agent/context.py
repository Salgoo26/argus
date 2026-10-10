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
    # api-spec 2-3 context — 예: 첨부 다운로드의 대상 탐지건 {"target": {"detection_id": 12}}
    context: dict | None = None
    # 기록 제외 라우트에서 **거부된 시도만** 남길 때의 (수행업무, 데이터 유형)
    # — record_refused_change
    refused: tuple[str, str] | None = None


_current: ContextVar[AccessRecord | None] = ContextVar("argus_access_record", default=None)


def start_record(record: AccessRecord):
    return _current.set(record)


def end_record(token) -> None:
    _current.reset(token)


def record_actor(login_id: str) -> None:
    record = _current.get()
    if record is not None:
        record.actor_login_id = login_id


def record_query_keys(keys: list[str]) -> None:
    """검색 조건의 **키 이름만** — 조건을 URL이 아니라 요청 본문으로 받는 검색용 (policy 6-2)"""
    record = _current.get()
    if record is not None:
        record.query_keys = list(keys)


def record_subject_count(count: int) -> None:
    """화면에 보여준(마스킹된) 정보주체 식별값의 수"""
    record = _current.get()
    if record is not None:
        record.subject_count = count


def record_target(detection_id: int) -> None:
    """무엇을 대상으로 했는지 — 경로 변수 값은 원장에 남기지 않으므로(라우트 템플릿) context로"""
    record = _current.get()
    if record is not None:
        record.context = {"target": {"detection_id": detection_id}}


def record_refused_change(detection_id: int) -> None:
    """상태 변경(기록 제외 라우트)이 **규칙 때문에 거부된** 시도 — UPDATE·ACCESS_LOG FAILURE로.
    성공한 변경은 상태 이력이 증적이지만, 거부된 시도는 이력에 남지 않으므로 자체 기록으로
    (v0.1 보강 H — 담당자의 본인 건 처리 시도). 입력한 사유 문장은 싣지 않는다"""
    record = _current.get()
    if record is not None:
        record.refused = ("UPDATE", "ACCESS_LOG")
        record.context = {"target": {"detection_id": detection_id}}


def record_report(report_id: int) -> None:
    """점검 보고서 생성·열람 — 어떤 보고서였는지 (api-spec 2-3 context.report_id)"""
    record = _current.get()
    if record is not None:
        record.context = {"report_id": report_id}
