"""SQL 분석 — 정규화·수행업무·테이블·데이터 유형
(architecture 3-4 "원장 기록"·"수행업무 매핑"·"기록 제외")

PostgreSQL 자체 파서를 쓰는 pglast로 읽는다
— 정규식으로 SQL을 해석하면 주석·문자열 안의 키워드에 속는다.

- 정규화: 리터럴(문자열·숫자·비트·달러 인용)을 $n으로, 주석은 지운다. Argus에는 이것만 간다 — 원문은
  리터럴에 이메일·이름이 실릴 수 있어 게이트웨이 원문 저장소에만 둔다 (절대 규칙 #3)
- 기록 제외(Argus 미전송, 원문은 저장): 사용자 테이블을 하나도 참조하지 않는 문장
  (카탈로그·`SELECT version()`),
  트랜잭션 제어·SET·SHOW, DDL·DCL(v0.1 — 수행업무 코드 없음).
  **단 사용자 함수·`DO`·`CALL`은 보낸다** —
  테이블 이름 없이도 내부에서 개인정보를 처리할 수 있어서 (2026-10-06)
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from pglast import ast, parse_sql, split
from pglast.parser import ParseError, scan
from pglast.visitors import Visitor

SQL_NORMALIZED_MAX = 4000  # api-spec 2-2
UNPARSABLE = "-- statement could not be parsed"
NORMALIZE_FAILED = "-- statement could not be normalized"

SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema"})
_CONSTANT_TOKENS = frozenset({"SCONST", "USCONST", "ICONST", "FCONST", "BCONST", "XCONST"})
_COMMENT_TOKENS = frozenset({"C_COMMENT", "SQL_COMMENT"})
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")

# 데이터 유형 — 문장이 건드린 테이블 중 가장 민감한 것 (architecture 3-4 "데이터 유형")
# v0.1 보강 N: 판정은 Argus 보호 대상 등록부에서 받은 표로 한다(app/registry.py). 이 표는 등록부를
# 한 번도 받지 못했을 때의 기본값(builtin)이자 Argus 초기 등록 데이터의 원본
TABLE_CATEGORY = {
    "refund_account": "PAYMENT",  # 계좌번호는 암호문이지만 결제수단 테이블 접근 자체가 점검 대상
    "member": "MEMBER_BASIC",
    "member_consent": "MEMBER_BASIC",
    "retained_member_record": "MEMBER_BASIC",
    # 배송지 (2026-10-07, 플랫폼 0007) — 빠지면 NONE이 되어 DB 직접 접근 탐지에서 누락된다
    "shipping_address": "MEMBER_BASIC",
    "inquiry": "INQUIRY",
    "orders": "ORDER",
    "payment": "ORDER",  # PG 거래 정보
}
SENSITIVITY = ["NONE", "ORDER", "INQUIRY", "MEMBER_BASIC", "PAYMENT"]

# 문장 종류 → 수행업무 (api-spec 2-3)
_ACTIONS = {
    ast.SelectStmt: "READ",
    ast.InsertStmt: "CREATE",
    ast.UpdateStmt: "UPDATE",
    ast.MergeStmt: "UPDATE",
    ast.DeleteStmt: "DELETE",
    ast.TruncateStmt: "DELETE",
}
# 안을 볼 수 없는 실행 — 수정 가능성이 있어 UPDATE, 개인정보 처리로 가정 (2026-10-06 사용자 결정)
_OPAQUE = (ast.CallStmt, ast.DoStmt, ast.ExecuteStmt)
_CONTROL = (
    ast.TransactionStmt,
    ast.VariableSetStmt,
    ast.VariableShowStmt,
    ast.DiscardStmt,
    ast.DeallocateStmt,
    ast.PrepareStmt,
    ast.ListenStmt,
    ast.UnlistenStmt,
    ast.NotifyStmt,
    ast.CheckPointStmt,
)


@dataclass(frozen=True)
class Analysis:
    action: str | None  # None = Argus에 보내지 않음 (원문만 저장)
    skip_reason: str | None
    data_category: str = "NONE"
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()  # UPDATE·INSERT 대상 컬럼 (반환 컬럼은 결과 설명에서 따로)
    opaque: bool = False  # 사용자 함수·DO·CALL — 내부에서 무엇을 처리했는지 알 수 없음
    normalized: str = ""


def split_statements(sql: str) -> list[str]:
    """단순 질의(Query)에 여러 문장이 들어 있으면 나눈다. 못 나누면 통째로 하나"""
    try:
        return [s for s in split(sql) if s.strip()] or [sql]
    except ParseError:
        return [sql]


def normalize(sql: str) -> str:
    """리터럴 → $n(기존 매개변수 번호 다음부터), 주석 삭제, 공백 정리"""
    try:
        tokens = scan(sql)
    except ParseError:
        return UNPARSABLE
    used = [int(sql[t.start + 1 : t.end + 1]) for t in tokens if t.name == "PARAM"]
    next_param = max(used, default=0) + 1
    parts, end = [], 0
    for token in tokens:
        if token.start > end and parts:
            parts.append(" ")  # 토큰 사이에 공백·주석이 있던 자리 — 공백 하나로
        end = token.end + 1
        if token.name in _COMMENT_TOKENS:
            continue
        if token.name in _CONSTANT_TOKENS:
            parts.append(f"${next_param}")
            next_param += 1
        else:
            parts.append(sql[token.start : end])
    normalized = re.sub(r" {2,}", " ", "".join(parts)).strip()
    # 따옴표가 남았다면(예: 따옴표가 든 식별자) 원문 조각일 수 있다
    # — Argus 검증과 같은 기준으로 막는다
    if "'" in normalized or _DOLLAR_QUOTE.search(normalized):
        return NORMALIZE_FAILED
    return normalized[:SQL_NORMALIZED_MAX]


class _References(Visitor):
    def __init__(self, user_functions: frozenset[str]) -> None:
        super().__init__()
        self.user_functions = user_functions
        self.tables: list[str] = []
        self.ctes: set[str] = set()
        self.calls_user_function = False

    def visit_CommonTableExpr(self, ancestors, node):
        self.ctes.add(node.ctename)

    def visit_RangeVar(self, ancestors, node):
        name = f"{node.schemaname}.{node.relname}" if node.schemaname else node.relname
        self.tables.append(name)

    def visit_FuncCall(self, ancestors, node):
        names = [n.sval for n in node.funcname]
        if (len(names) == 1 or names[0] not in SYSTEM_SCHEMAS) and names[-1] in self.user_functions:
            self.calls_user_function = True


def _is_system_table(name: str) -> bool:
    schema, _, relname = name.rpartition(".")
    return schema in SYSTEM_SCHEMAS or (not schema and relname.startswith("pg_"))


def category_of(
    tables: list[str] | tuple[str, ...],
    opaque: bool = False,
    table_category: Mapping[str, str] | None = None,
) -> str:
    """table_category: 보호 대상 등록부에서 받은 표(v0.1 보강 N-3). 없으면 고정 표(builtin)"""
    known = TABLE_CATEGORY if table_category is None else table_category
    found = [known.get(t.rpartition(".")[2], "NONE") for t in tables]
    if opaque:
        found.append("MEMBER_BASIC")
    return max(found, key=SENSITIVITY.index, default="NONE")


def _target_columns(stmt) -> list[str]:
    if isinstance(stmt, ast.UpdateStmt):
        targets = stmt.targetList or ()
    elif isinstance(stmt, ast.InsertStmt):
        targets = stmt.cols or ()
    else:
        return []
    table = stmt.relation.relname
    return [f"{table}.{t.name}" for t in targets if getattr(t, "name", None)]


def analyze(
    sql: str,
    user_functions: frozenset[str] = frozenset(),
    table_category: Mapping[str, str] | None = None,
) -> Analysis:
    normalized = normalize(sql)
    try:
        statements = parse_sql(sql)
    except ParseError:
        # 파서가 못 읽은 문장 — 보통은 DB도 거부한다(FAILURE로 남음).
        # 실행됐다면 무엇을 했는지 모르므로
        # 개인정보 처리로 가정해 보낸다 (안을 볼 수 없는 실행과 같은 처리)
        return Analysis("READ", None, "MEMBER_BASIC", opaque=True, normalized=UNPARSABLE)
    if len(statements) != 1:
        return Analysis(None, "MULTIPLE_STATEMENTS", normalized=normalized)

    stmt = statements[0].stmt
    if isinstance(stmt, ast.ExplainStmt):
        stmt = stmt.query  # EXPLAIN ANALYZE는 실제로 실행한다 — 안의 문장으로 판단
    if isinstance(stmt, _CONTROL):
        return Analysis(None, "CONTROL", normalized=normalized)

    refs = _References(user_functions)
    refs(stmt)
    tables = tuple(dict.fromkeys(t for t in refs.tables if t not in refs.ctes))
    user_tables = tuple(t for t in tables if not _is_system_table(t))

    if isinstance(stmt, _OPAQUE):
        action, opaque = "UPDATE", True
    elif isinstance(stmt, ast.CopyStmt):
        action, opaque = ("CREATE" if stmt.is_from else "DOWNLOAD"), refs.calls_user_function
    elif type(stmt) in _ACTIONS:
        action, opaque = _ACTIONS[type(stmt)], refs.calls_user_function
    else:
        # DDL·DCL — v0.1은 원문 저장소에만 (수행업무 코드가 없음, v0.2 명령어 통제와 함께)
        return Analysis(None, "DDL_DCL", normalized=normalized)

    if not user_tables and not opaque:
        return Analysis(None, "NO_USER_TABLE", normalized=normalized)
    return Analysis(
        action,
        None,
        category_of(user_tables, opaque, table_category),
        user_tables,
        tuple(_target_columns(stmt)),
        opaque,
        normalized,
    )
