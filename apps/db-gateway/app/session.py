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
from dataclasses import dataclass
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


def parse_row_description(body: bytes) -> list[tuple[int, int]]:
    (count,) = struct.unpack("!H", body[:2])
    pos, columns = 2, []
    for _ in range(count):
        _, pos = _cstr(body, pos)
        table_oid, attnum = struct.unpack("!Ih", body[pos : pos + 6])
        columns.append((table_oid, attnum))
        pos += 18
    return columns


@dataclass
class Execution:
    sql: str
    protocol: str  # "simple" | "extended"
    started_at: datetime
    statement: str = ""  # 확장 질의의 문장 이름 ("" = 이름 없음)
    portal: str = ""
    params: list | None = None
    param_types: list[int] | None = None
    columns: list[tuple[int, int]] | None = None  # 결과 컬럼 (테이블 OID, 컬럼 번호)
    rows: int = 0  # DB 툴로 흘려보낸 행 수 (나눠 가져오는 실행이면 누적)


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
        self.portals: dict[str, tuple[str, list[bytes | None], list[int]]] = {}
        self.row_descriptions: dict[str, list[tuple[int, int]]] = {}
        self.pending: deque = deque()
        self.suspended: dict[str, Execution] = {}

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
                statement, values, formats = self.portals.get(portal, ("", [], []))
                sql, oids = self.prepared.get(statement, ("", []))
                params = decode_params(values, formats, oids)
                execution = Execution(
                    sql, "extended", self.clock(), statement, portal, params, oids
                )
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
        self.portals[portal] = (statement, values, formats)
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

            columns = await self.catalog.column_names(execution.columns or [])
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
            )
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
) -> dict:
    """문장 1건의 접속기록 (api-spec 2-2 "access_path=DB 기록 규칙")

    실제 중계(Session)와 기준선 시드(seed_baseline.py)가 같은 함수로 만든다 — 형식이 갈라지지 않게
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
    if processed:
        # 회원번호 추출 전이라(구현 순서 ② 보류) 처리한 정보주체를 특정하지 못한다 — 건수만 남긴다
        # (api-spec 2-2 "정보주체 미특정")
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
        "subject": {"type": "MEMBER", "ids": [], "count": row_count if processed else 0},
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
