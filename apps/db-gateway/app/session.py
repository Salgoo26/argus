"""연결 하나의 문장 추적 — 어떤 SQL이 실행됐고 어떻게 끝났는지
(architecture 3-4 "원장 기록"·"기록 실패 시")

DB 툴과 DB가 주고받는 메시지를 보며 "실행 단위"를 맞춘다:
- 단순 질의(Query): SQL 문자열 하나에 문장이 여럿일 수 있다 → 문장마다 하나
- 확장 질의(Parse → Bind → Execute → Sync): DBeaver·JDBC가 쓰는 방식.
  Parse에 SQL, Bind에 매개변수가 있고
  이름 있는 문장은 나중에 Bind·Execute만 다시 온다
  (pgjdbc는 같은 문장을 5번 넘게 실행하면 이렇게 바꾼다 —
  2026-10-06 실측) → 연결별로 "문장 이름 → SQL·결과 컬럼 설명"을 기억한다
- DB는 실행 순서대로 답한다: 완료(CommandComplete)·오류(ErrorResponse)가 올 때마다
  맨 앞 실행이 끝난 것.
  오류가 나면 DB는 다음 Sync까지 나머지를 건너뛴다 → 그 실행들은 일어나지 않았으므로 기록하지 않는다

처리한 정보주체(v0.1 보강 G-1): 결과 열 설명(RowDescription)의 테이블 OID·열 번호로 회원을
가리키는 열을 찾고, 결과 행(DataRow)에서 그 열의 값만 읽어 회원번호로 남긴다. SQL은 해석하지 않는다.
회원 열이 없거나(식·함수 결과 포함) 값을 해석하지 못하면 지금처럼 미특정 + 건수.

기록 시점: 완료 신호를 DB 툴에 넘기기 **전에** 원문·접속기록을 남긴다.
실패하면 완료 신호 대신 오류를 보내고
연결을 끊는다 — 결과 행은 이미 흘렀어도 DB 툴에서는 쿼리 실패가 된다
(fail-closed, 3티어 3-2와 같은 원칙)
"""

import asyncio
import logging
import re
import struct
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.catalog import Catalog
from app.sql import Analysis, analyze, split_statements
from app.store import Store

logger = logging.getLogger("gateway.session")

TABLES_MAX, COLUMNS_MAX = 50, 200  # api-spec 2-2
_DB_OBJECT = re.compile(r'^[A-Za-z0-9_."]{1,128}$')
# 완료 태그의 건수가 "영향받은 행"인 명령 — 그 밖(SELECT·FETCH 등)은 DB 툴로 흘려보낸 행 수를 센다
_COUNTED_BY_TAG = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "COPY"})
_SYNC = object()  # Sync·단순 질의의 끝 — DB가 ReadyForQuery로 답하는 경계


def _cstr(body: bytes, pos: int) -> tuple[str, int]:
    end = body.index(b"\0", pos)
    return body[pos:end].decode("utf-8", "replace"), end + 1


_BINARY_DECODERS: dict[int, Callable[[bytes], str]] = {
    16: lambda b: "true" if b == b"\x01" else "false",  # bool
    20: lambda b: str(struct.unpack("!q", b)[0]),  # int8
    21: lambda b: str(struct.unpack("!h", b)[0]),  # int2
    23: lambda b: str(struct.unpack("!i", b)[0]),  # int4
    25: lambda b: b.decode("utf-8", "replace"),  # text
    1043: lambda b: b.decode("utf-8", "replace"),  # varchar
    2950: lambda b: str(uuid.UUID(bytes=b)),  # uuid
}


def decode_params(values: list[bytes | None], formats: list[int], oids: list[int]) -> list:
    """원문 저장용 매개변수 — 바이너리 형식(pgjdbc는 정수를 바이너리로 보낸다)은 타입대로 읽는다"""
    decoded = []
    for i, value in enumerate(values):
        if value is None:
            decoded.append(None)
            continue
        fmt = formats[0] if len(formats) == 1 else (formats[i] if i < len(formats) else 0)
        oid = oids[i] if i < len(oids) else 0
        if fmt == 0:
            decoded.append(value.decode("utf-8", "replace"))
        else:
            try:
                decoded.append(_BINARY_DECODERS[oid](value))
            except (KeyError, struct.error, ValueError):
                decoded.append("0x" + value.hex())
    return decoded


@dataclass(frozen=True)
class Column:
    """결과 열 하나 (RowDescription) — 어느 테이블의 몇 번째 열인지, 값의 타입"""

    table_oid: int  # 식·계산 열은 0
    attnum: int
    type_oid: int


def parse_row_description(body: bytes) -> list[Column]:
    (count,) = struct.unpack("!H", body[:2])
    pos, columns = 2, []
    for _ in range(count):
        _, pos = _cstr(body, pos)
        # 테이블 OID(4)·열 번호(2)·타입 OID(4)·타입 크기(2)·타입 수식자(4)·형식(2) = 18바이트.
        # 형식은 쓰지 않는다 — 문장 Describe의 형식은 늘 0이라, 실제 형식은 Bind가 정한다
        table_oid, attnum, type_oid = struct.unpack("!IhI", body[pos : pos + 10])
        columns.append(Column(table_oid, attnum, type_oid))
        pos += 18
    return columns


# ── 회원번호 추출 (v0.1 보강 G-1) ─────────────────────────
# SQL을 해석하지 않는다. 결과 열 설명으로 회원을 가리키는 열(catalog.MEMBER_COLUMNS)을 찾고,
# 결과 행에서 그 열의 값만 읽는다. 다른 열의 값은 읽지도 남기지도 않는다
SUBJECT_IDS_MAX = 1000  # api-spec 2-2 — 넘으면 앞 1,000개만, count는 전체
_INTEGER_TYPES = {20: "!q", 21: "!h", 23: "!i"}  # int8·int2·int4 (바이너리 형식)
_DIGITS = re.compile(r"^-?[0-9]{1,19}$")


class SubjectError(ValueError):
    """회원 열의 값을 해석할 수 없음 — 그 실행은 미특정으로 남긴다"""


def decode_subject(value: bytes, fmt: int, type_oid: int) -> str:
    if fmt == 0:
        text = value.decode("ascii", "replace")
        if not _DIGITS.match(text):
            raise SubjectError("member reference is not an integer")
        return str(int(text))
    layout = _INTEGER_TYPES.get(type_oid)
    if layout is None or len(value) != struct.calcsize(layout):
        raise SubjectError("unsupported binary member reference")
    return str(struct.unpack(layout, value)[0])


def _result_format(formats: list[int], index: int) -> int:
    """Bind의 결과 형식 코드 — 없으면 전부 텍스트, 하나면 전부 그 형식, 아니면 열마다"""
    if not formats:
        return 0
    return formats[0] if len(formats) == 1 else (formats[index] if index < len(formats) else 0)


def _data_row_values(body: bytes, wanted: set[int]) -> dict[int, bytes | None]:
    """DataRow에서 원하는 열의 값만 꺼낸다 — 나머지 값은 건너뛴다"""
    (count,) = struct.unpack("!H", body[:2])
    pos, found = 2, {}
    for index in range(count):
        if index > max(wanted):
            break
        (length,) = struct.unpack("!i", body[pos : pos + 4])
        pos += 4
        if index in wanted:
            found[index] = None if length < 0 else body[pos : pos + length]
        pos += max(length, 0)
    return found


@dataclass
class Execution:
    sql: str
    protocol: str  # "simple" | "extended"
    started_at: datetime
    statement: str = ""  # 확장 질의의 문장 이름 ("" = 이름 없음)
    portal: str = ""
    params: list | None = None
    param_types: list[int] | None = None
    columns: list[Column] | None = None  # 결과 열 설명
    result_formats: list[int] = field(default_factory=list)  # Bind의 결과 형식 (단순 질의 = 텍스트)
    rows: int = 0  # DB 툴로 흘려보낸 행 수 (나눠 가져오는 실행이면 누적)
    # 회원번호 추출 (v0.1 보강 G-1) — member_index가 None이면 아직 열을 보지 않음
    member_index: list[int] | None = None
    subject_ids: set[str] = field(default_factory=set)
    subject_failed: bool = False  # 값을 해석하지 못함 → 미특정


class Session:
    def __init__(
        self,
        *,
        store: Store,
        catalog: Catalog,
        login_id: str,
        client_ip: str | None,
        token_id: str | None,
        db_user: str,
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.login_id = login_id
        self.client_ip = client_ip
        self.token_id = token_id
        self.db_user = db_user
        self.clock = clock
        self.prepared: dict[str, tuple[str, list[int]]] = {}
        # 포털 → (문장 이름, 매개변수 값, 매개변수 형식, 결과 형식)
        self.portals: dict[str, tuple[str, list[bytes | None], list[int], list[int]]] = {}
        self.row_descriptions: dict[str, list[Column]] = {}
        self.pending: deque = deque()
        self.suspended: dict[str, Execution] = {}
        # 회원을 가리키는 열 — 이 연결에서 처음 결과를 볼 때 카탈로그에서 받아 둔다 (G-1)
        self.member_columns: frozenset[tuple[int, int]] | None = None

    # ── DB 툴 → DB ──

    def from_client(self, tag: bytes, body: bytes) -> str | None:
        """상태만 갱신한다(대기 없음). 받아들일 수 없는 메시지면 거부 사유를 돌려준다"""
        if tag == b"Q":
            sql, _ = _cstr(body, 0)
            now = self.clock()
            for statement in split_statements(sql):
                self.pending.append(Execution(statement, "simple", now))
            self.pending.append(_SYNC)
        elif tag == b"P":
            name, pos = _cstr(body, 0)
            sql, pos = _cstr(body, pos)
            (count,) = struct.unpack("!H", body[pos : pos + 2])
            oids = list(struct.unpack(f"!{count}I", body[pos + 2 : pos + 2 + 4 * count]))
            self.prepared[name] = (sql, oids)
            self.row_descriptions.pop(name, None)
        elif tag == b"B":
            self._bind(body)
        elif tag == b"E":
            portal, _ = _cstr(body, 0)
            if portal in self.suspended:  # 나눠 가져오는 실행의 다음 묶음
                self.pending.append(self.suspended.pop(portal))
            else:
                statement, values, formats, result_formats = self.portals.get(
                    portal, ("", [], [], [])
                )
                sql, oids = self.prepared.get(statement, ("", []))
                params = decode_params(values, formats, oids)
                execution = Execution(
                    sql, "extended", self.clock(), statement, portal, params, oids
                )
                execution.result_formats = result_formats
                execution.columns = self.row_descriptions.get(statement) if statement else None
                self.pending.append(execution)
        elif tag == b"S":
            self.pending.append(_SYNC)
        elif tag == b"C":
            kind, name = chr(body[0]), _cstr(body, 1)[0]
            if kind == "S":
                self.prepared.pop(name, None)
                self.row_descriptions.pop(name, None)
            else:
                self.portals.pop(name, None)
                self.suspended.pop(name, None)
        elif tag == b"F":
            # 함수 직접 호출(fastpath) — 무엇을 하는지 기록할 수 없어 받지 않는다
            return "FASTPATH"
        return None

    def _bind(self, body: bytes) -> None:
        portal, pos = _cstr(body, 0)
        statement, pos = _cstr(body, pos)
        (nfmt,) = struct.unpack("!H", body[pos : pos + 2])
        formats = list(struct.unpack(f"!{nfmt}H", body[pos + 2 : pos + 2 + 2 * nfmt]))
        pos += 2 + 2 * nfmt
        (nval,) = struct.unpack("!H", body[pos : pos + 2])
        pos += 2
        values: list[bytes | None] = []
        for _ in range(nval):
            (length,) = struct.unpack("!i", body[pos : pos + 4])
            pos += 4
            if length < 0:
                values.append(None)
            else:
                values.append(body[pos : pos + length])
                pos += length
        # 결과 열 형식 (0 텍스트 / 1 바이너리) — 회원번호 값을 읽을 때 쓴다 (G-1)
        (nres,) = struct.unpack("!H", body[pos : pos + 2])
        result_formats = list(struct.unpack(f"!{nres}H", body[pos + 2 : pos + 2 + 2 * nres]))
        self.portals[portal] = (statement, values, formats, result_formats)
        self.suspended.pop(portal, None)

    # ── DB → DB 툴 ──

    def _front(self) -> Execution | None:
        return self.pending[0] if self.pending and self.pending[0] is not _SYNC else None

    def _pop(self) -> Execution | None:
        return self.pending.popleft() if self._front() else None

    async def from_server(self, tag: bytes, body: bytes) -> bool:
        """완료·오류면 기록한 뒤 True. 기록 실패면 False — 호출 측이 완료 대신 오류를 보낸다"""
        if tag == b"T":
            execution = self._front()
            if execution and execution.columns is None:
                execution.columns = parse_row_description(body)
                if execution.statement:
                    self.row_descriptions[execution.statement] = execution.columns
        elif tag == b"D":
            execution = self._front()
            if execution:
                execution.rows += 1
                await self._collect_subjects(execution, body)
        elif tag == b"C":
            execution = self._pop()
            if execution:
                return await self._record(execution, command_tag=_cstr(body, 0)[0])
        elif tag == b"s":  # PortalSuspended — 행 수 제한으로 멈춤, 다음 Execute에서 이어감
            execution = self._pop()
            if execution:
                self.suspended[execution.portal] = execution
        elif tag == b"I":  # 빈 질의
            self._pop()
        elif tag == b"E":
            execution = self._pop()
            if execution:
                code = _error_code(body)
                return await self._record(execution, error_code=code)
        elif tag == b"Z":
            # 오류로 건너뛴 실행은 일어나지 않았다 — 기록 없이 버린다
            while self.pending and self.pending[0] is not _SYNC:
                self.pending.popleft()
            if self.pending:
                self.pending.popleft()
        return True

    async def _member_index(self, execution: Execution) -> list[int]:
        """이 실행의 결과 중 회원을 가리키는 열의 위치 — 처음 한 번 계산한다"""
        if execution.member_index is None:
            if self.member_columns is None:
                try:
                    self.member_columns = await self.catalog.member_columns()
                except Exception:
                    # 카탈로그를 못 보면 추출하지 않는다 — 사용자 질의는 막지 않고 미특정으로 남긴다
                    logger.warning("member column lookup failed — subjects stay unresolved")
                    execution.subject_failed = True
                    execution.member_index = []
                    return []
            execution.member_index = [
                i
                for i, column in enumerate(execution.columns or [])
                if (column.table_oid, column.attnum) in self.member_columns
            ]
        return execution.member_index

    async def _collect_subjects(self, execution: Execution, body: bytes) -> None:
        if execution.subject_failed or execution.columns is None:
            return
        index = await self._member_index(execution)
        if not index:
            return
        try:
            for i, value in _data_row_values(body, set(index)).items():
                if value is None:
                    continue  # 탈퇴로 끊긴 주문 등 — 회원 없음
                fmt = _result_format(execution.result_formats, i)
                execution.subject_ids.add(decode_subject(value, fmt, execution.columns[i].type_oid))
        except (SubjectError, struct.error):
            # 해석하지 못한 값이 하나라도 있으면 그 실행은 미특정 — 일부만 적어 오해를 사지 않게
            execution.subject_failed = True
            execution.subject_ids.clear()

    async def _subjects_of(self, execution: Execution) -> set[str] | None:
        """처리한 회원번호. None = 특정하지 못함(회원 열 없음·해석 실패·결과 열 없음)"""
        if execution.columns is None:
            return None  # 결과를 돌려주지 않는 실행(UPDATE 등) — 건수만
        index = await self._member_index(execution)
        if execution.subject_failed or not index:
            return None
        return execution.subject_ids

    async def _record(
        self, execution: Execution, command_tag: str | None = None, error_code: str | None = None
    ) -> bool:
        try:
            functions = await self.catalog.user_functions()
            analysis = analyze(execution.sql, functions)
            failed = command_tag is None
            row_count = 0 if failed else _row_count(command_tag, execution.rows)
            event_id = str(uuid.uuid4())
            occurred_at = execution.started_at.isoformat()
            raw = {
                "kind": "STATEMENT",
                "event_id": event_id,
                "occurred_at": occurred_at,
                "login_id": self.login_id,
                "client_ip": self.client_ip,
                "token_id": self.token_id,
                "protocol": execution.protocol,
                "statement_name": execution.statement,
                "sql": execution.sql,
                "params": execution.params,
                "param_types": execution.param_types,
                "result": "FAILURE" if failed else "SUCCESS",
                "command_tag": command_tag,
                "error_code": error_code,
                "row_count": row_count,
                "sent": analysis.action is not None,
                "skip_reason": analysis.skip_reason,
            }
            if analysis.action is None:
                await asyncio.to_thread(self.store.record_raw, raw)
                return True

            columns = await self.catalog.column_names(
                [(c.table_oid, c.attnum) for c in execution.columns or []]
            )
            subject_ids = None if failed else await self._subjects_of(execution)
            event = statement_event(
                event_id=event_id,
                occurred_at=occurred_at,
                login_id=self.login_id,
                client_ip=self.client_ip,
                token_id=self.token_id,
                db_user=self.db_user,
                analysis=analysis,
                row_count=row_count,
                failed=failed,
                columns=columns,
                subject_ids=subject_ids,
            )
            # 원문 저장소(raw)에는 결과 값을 넣지 않는다 — 회원번호는 원장(event)으로만
            await asyncio.to_thread(self.store.record, raw, event)
            return True
        except Exception:
            logger.exception("failed to record statement")
            return False


def statement_event(
    *,
    event_id: str,
    occurred_at: str,
    login_id: str,
    client_ip: str | None,
    token_id: str | None,
    db_user: str,
    analysis: Analysis,
    row_count: int,
    failed: bool,
    columns: list[str],
    subject_ids: set[str] | None = None,
) -> dict:
    """문장 1건의 접속기록 (api-spec 2-2 "access_path=DB 기록 규칙")

    실제 중계(Session)와 기준선 시드(seed_baseline.py)가 같은 함수로 만든다 — 형식이 갈라지지 않게.
    subject_ids: 결과에서 읽은 회원번호(v0.1 보강 G-1). None이면 특정하지 못한 것 — 건수만 남긴다
    """
    tables = [t for t in analysis.tables if _DB_OBJECT.fullmatch(t)][:TABLES_MAX]
    all_columns = list(dict.fromkeys([*columns, *analysis.columns]))
    context = {
        "db_user": db_user,
        "sql_normalized": analysis.normalized,
        "tables": tables,
        "columns": [c for c in all_columns if _DB_OBJECT.fullmatch(c)][:COLUMNS_MAX],
        "row_count": row_count,
    }
    processed = not failed and analysis.data_category != "NONE"
    subject: dict = {"type": "MEMBER", "ids": [], "count": row_count if processed else 0}
    if processed and subject_ids is not None:
        # 3티어와 같은 규칙 — 고유 회원번호, 1,000개 넘으면 앞 1,000개만·truncated, count는 전체
        ids = sorted(subject_ids, key=lambda s: (len(s), s))
        subject = {
            "type": "MEMBER",
            "ids": ids[:SUBJECT_IDS_MAX],
            "count": len(ids),
            "truncated": len(ids) > SUBJECT_IDS_MAX,
        }
        context["subject_unresolved"] = False
    elif processed:
        # 결과에 회원을 가리키는 열이 없거나 해석하지 못함 — 건수만 남긴다
        # (api-spec 2-2 "정보주체 미특정", 원문 SQL은 게이트웨이 저장소에)
        context["subject_unresolved"] = True
    if token_id:
        context["token_id"] = token_id
    return {
        "event_id": event_id,
        "occurred_at": occurred_at,
        "actor": {"login_id": login_id},
        "client_ip": client_ip,
        "action": analysis.action,
        "access_path": "DB",
        "data_category": analysis.data_category,
        "result": "FAILURE" if failed else "SUCCESS",
        "subject": subject,
        "context": context,
    }


def _row_count(command_tag: str, streamed_rows: int) -> int:
    parts = command_tag.split()
    if parts and parts[0] in _COUNTED_BY_TAG and parts[-1].isdigit():
        return int(parts[-1])
    return streamed_rows


def _error_code(body: bytes) -> str | None:
    pos = 0
    while pos < len(body) and body[pos] != 0:
        end = body.index(b"\0", pos + 1)
        if body[pos : pos + 1] == b"C":
            return body[pos + 1 : end].decode("ascii", "replace")
        pos = end + 1
    return None
