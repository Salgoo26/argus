"""접속기록 해시체인 — 정규화(canonical) 규칙과 계산·검증 (§8③ 위·변조 방지)

정규화 규칙 v1 (db-schema 8절 미결 사항을 M1에서 확정 — implementation-log 2026-09-29):
- 대상: access_log의 hash를 제외한 모든 컬럼 (HASHED_FIELDS). id·prev_hash·received_at 포함
  - id를 넣는 이유: 탐지 배치 커서가 id라서, 레코드 순서를 바꿔치기해도 드러나게
  - prev_hash를 넣어 앞 레코드와 연결 → 중간 레코드 수정·삭제 시 이후 체인 전체가 깨짐
- 값 표기: 시각 = UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` / IP = Python ipaddress 정규형 /
  UUID = 소문자 하이픈 형식 / NULL = JSON null / 배열은 순서 유지
- 직렬화: JSON, 키 정렬, 공백 없음(`,` `:`), UTF-8 (ensure_ascii=False)
- hash = SHA-256(직렬화 바이트)의 소문자 hex 64자
- 첫 레코드(또는 파기 후 앵커 없는 시작)의 prev_hash는 NULL

같은 함수를 INSERT 직전(append)과 검증(verify_chain) 양쪽에서 쓴다 → 규칙이 한 곳에만 존재.
"""

import hashlib
import ipaddress
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from app.models import access_log

HASHED_FIELDS = (
    "id",
    "event_id",
    "source_system_id",
    "access_path",
    "actor_login_id",
    "occurred_at",
    "client_ip",
    "subject_type",
    "subject_ids",
    "subject_count",
    "subject_truncated",
    "action",
    "data_category",
    "result",
    "request_method",
    "request_path",
    "request_query_keys",
    "context",
    "received_at",
    "prev_hash",
)
_TIMESTAMP_FIELDS = {"occurred_at", "received_at"}


def _normalize(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in _TIMESTAMP_FIELDS:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field} must be a timezone-aware datetime")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if field == "event_id":
        return str(uuid.UUID(str(value)))
    if field == "client_ip":
        # DB에서 읽으면 문자열(또는 ipaddress 객체), 수신 시엔 정규화된 문자열 — 양쪽을 같은 형태로
        return ipaddress.ip_address(str(value).split("/")[0]).compressed
    if field == "prev_hash":
        return str(value).strip()
    return value


def canonical_bytes(record: dict[str, Any]) -> bytes:
    body = {field: _normalize(field, record.get(field)) for field in HASHED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def compute_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    checked: int
    broken_at_id: int | None = None
    reason: str | None = None


def verify_chain(conn: Connection, anchor_hash: str | None = None) -> ChainVerification:
    """원장 전체를 id 순으로 다시 계산해 체인이 이어지는지 확인한다.

    anchor_hash: 파기 후 남은 첫 레코드의 prev_hash (destruction_history.chain_anchor_hash).
    파기 전이면 None — 첫 레코드의 prev_hash가 NULL이어야 한다.
    """
    expected_prev = anchor_hash
    checked = 0
    rows = conn.execute(select(access_log).order_by(access_log.c.id)).mappings()
    for row in rows:
        record = dict(row)
        prev = record["prev_hash"].strip() if record["prev_hash"] else None
        if prev != expected_prev:
            return ChainVerification(False, checked, record["id"], "prev_hash does not link")
        stored = record["hash"].strip()
        if compute_hash(record) != stored:
            return ChainVerification(False, checked, record["id"], "hash mismatch (tampered)")
        expected_prev = stored
        checked += 1
    return ChainVerification(True, checked)
