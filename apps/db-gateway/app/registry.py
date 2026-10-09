"""보호 대상 등록부 — Argus에서 받아 쓰는 판정 표 (v0.1 보강 N-3)

판정 표 = 테이블 → 데이터 유형, 회원 식별 열(결과에서 회원번호를 읽는 열), 버전.
원래 이 게이트웨이 코드에 고정돼 있던 표(sql.TABLE_CATEGORY·catalog.MEMBER_COLUMNS)를 Argus의
등록부(담당자가 화면에서 관리)로 옮겼다.

받지 못할 때 (fail-closed — 기록이 NONE으로 빠져 탐지에서 누락되지 않게):
1. 마지막으로 받은 등록부를 쓴다 — 받을 때마다 gateway-data 볼륨에 저장해 재기동해도 남는다
2. 한 번도 받은 적이 없으면 고정 표(builtin)를 쓴다

v0.1 판정은 테이블 단위다 — 등록된 개인정보 컬럼이 하나라도 있는 테이블에 접근하면 그 테이블의
데이터 유형으로 기록한다. 컬럼 단위 판정은 v0.2(`SELECT *`·식·함수에서 누락 위험).
원장 context.registry_version에 판정에 쓴 버전을 남긴다.
"""

import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.catalog import MEMBER_COLUMNS
from app.sql import SENSITIVITY, TABLE_CATEGORY

logger = logging.getLogger("gateway.registry")

DB = "platform"  # 대상 DB의 논리 이름 — Argus 등록부(app/protection/registry.py)와 같은 값
BUILTIN_VERSION = "builtin"
_VERSION = re.compile(r"^r[0-9]{1,18}$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_CATEGORIES = frozenset(SENSITIVITY) - {"NONE"}


@dataclass(frozen=True)
class RegistrySnapshot:
    version: str
    tables: Mapping[str, str]  # 테이블 → 데이터 유형 (없는 테이블 = NONE)
    member_columns: tuple[tuple[str, str], ...]


BUILTIN = RegistrySnapshot(BUILTIN_VERSION, dict(TABLE_CATEGORY), tuple(MEMBER_COLUMNS))


class RegistryError(ValueError):
    pass


def parse(payload: Any) -> RegistrySnapshot:
    """Argus 응답 검증 — 이름·분류만 받는다(값 칸이 있으면 거부)"""
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "database",
        "tables",
        "member_columns",
    }:
        raise RegistryError("unexpected registry shape")
    if payload["database"] != DB:
        raise RegistryError("registry is for another database")
    version, tables, members = payload["version"], payload["tables"], payload["member_columns"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise RegistryError("invalid registry version")
    if not isinstance(tables, dict) or not all(
        isinstance(t, str) and _NAME.fullmatch(t) and c in _CATEGORIES for t, c in tables.items()
    ):
        raise RegistryError("invalid table categories")
    if not isinstance(members, list) or not all(
        isinstance(m, list)
        and len(m) == 2
        and all(isinstance(n, str) and _NAME.fullmatch(n) for n in m)
        for m in members
    ):
        raise RegistryError("invalid member columns")
    return RegistrySnapshot(version, dict(tables), tuple((t, c) for t, c in members))


def _to_payload(snapshot: RegistrySnapshot) -> dict:
    return {
        "version": snapshot.version,
        "database": DB,
        "tables": dict(snapshot.tables),
        "member_columns": [list(m) for m in snapshot.member_columns],
    }


class Registry:
    """지금 쓰는 판정 표 — 세션(asyncio)과 동기화 스레드가 함께 본다. 통째로 바꿔 끼우므로
    읽는 쪽은 잠금 없이 한 시점의 표를 얻는다"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._current = BUILTIN
        if path is not None and path.exists():
            try:
                self._current = parse(json.loads(path.read_text(encoding="utf-8")))
                logger.info("loaded last registry %s", self._current.version)
            except (OSError, ValueError):
                logger.exception("stored registry unreadable — using built-in table")

    @property
    def current(self) -> RegistrySnapshot:
        return self._current

    def apply(self, payload: Any) -> bool:
        """받은 등록부를 검증해 바꿔 끼우고 저장한다. 바뀌었으면 True"""
        snapshot = parse(payload)
        with self._lock:
            if snapshot == self._current:
                return False
            if self.path is not None:
                # 쓰다 끊겨도 이전 파일이 남게 — 임시 파일에 쓰고 바꿔 끼운다
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(_to_payload(snapshot), ensure_ascii=False), "utf-8")
                os.replace(tmp, self.path)
            self._current = snapshot
        logger.info("registry updated to %s", snapshot.version)
        return True
