"""Argus 자체 접속기록 미들웨어 (LOG-17, api-spec 2-7)

플랫폼 Agent와 같은 흐름이되, 기록을 outbox가 아니라 **원장에 직접 append**한다.
1. 요청 시작: 기록지(접속일시·접속지·검색조건 키 이름)를 contextvars에 둔다
2. 인증 의존성·핸들러가 식별자·정보주체 건수를 채운다
3. **응답 헤더를 보내기 직전**, 수집 API와 같은 건별 검증
   (validate_event, Argus 전용 코드 허용)을 거쳐
   같은 append 함수로 원장에 기록 — 해시체인에 함께 들어간다
   - 기록에 실패하면 원래 응답 대신 500 (fail-closed, architecture 3-2와 같은 원칙)
4. 처리되지 않은 예외는 FAILURE로 기록한 뒤 다시 던진다

기록하지 않는 경우: 식별자가 없는 요청(미인증, 없는 ID로의 로그인),
명시적 제외 라우트, 매칭 안 된 요청.
/ingest(시스템 간 수신)·/healthz는 /api 밖이라 대상이 아니다.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.agent.client_ip import IPNetwork, resolve_client_ip
from app.agent.context import AccessRecord, end_record, start_record
from app.agent.decorators import SESSION_ACTIONS, AccessLogSpec, is_api_path, spec_of
from app.errors import error_body
from app.ingest.validation import Rejection, validate_event
from app.ledger.append import append_access_logs
from app.models import source_system

logger = logging.getLogger(__name__)

SOURCE = "ARGUS"
QUERY_KEYS_LIMIT = 50
_QUERY_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.[]-")


def query_keys(query_string: bytes) -> list[str]:
    """검색조건의 **키 이름만** — 값(정보주체 ID 검색어 등)은 기록하지 않는다 (policy 6-2)"""
    keys: list[str] = []
    for key, _value in parse_qsl(query_string.decode("latin-1"), keep_blank_values=True):
        if 0 < len(key) <= 64 and set(key) <= _QUERY_KEY_CHARS and key not in keys:
            keys.append(key)
    return keys[:QUERY_KEYS_LIMIT]


def build_event(spec: AccessLogSpec, record: AccessRecord, route_path: str, result: str) -> dict:
    """api-spec 2-1 형식의 이벤트 — 수집 API와 같은 검증기를 통과시키기 위해 같은 모양으로 만든다"""
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": record.occurred_at.isoformat(timespec="microseconds"),
        "actor": {"login_id": record.actor_login_id},
        "client_ip": record.client_ip,
        "action": spec.action,
        "access_path": "APP",
        "data_category": spec.data_category,
        # 라우트 템플릿(/api/detections/{detection_id}) — 경로 변수 값이 원장에 남지 않게
        "request": {"method": record.method, "path": route_path, "query_keys": record.query_keys},
        "result": result,
        "context": record.context or {},
    }
    if spec.action not in SESSION_ACTIONS:
        # 건수만, ids 없음 — 회원 PK를 Argus 원장에 다시 쌓지 않는다 (api-spec 2-7, policy 6-3)
        event["subject"] = {"type": "MEMBER", "count": record.subject_count}
    return event


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp, trusted_proxies: tuple[IPNetwork, ...] = ()) -> None:
        self.app = app
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_api_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        record = AccessRecord(
            occurred_at=datetime.now(UTC),
            client_ip=resolve_client_ip(
                client[0] if client else "",
                Headers(scope=scope).get("x-forwarded-for"),
                self.trusted_proxies,
            ),
            method=scope["method"],
            query_keys=query_keys(scope.get("query_string", b"")),
        )
        token = start_record(record)
        started = False
        replaced = False

        async def send_with_log(message: Message) -> None:
            nonlocal started, replaced
            if message["type"] == "http.response.start" and not started:
                started = True
                if not await self._log(scope, record, message["status"]):
                    replaced = True
                    await _send_log_failure(send)
                    return
            if replaced:
                return
            await send(message)

        try:
            await self.app(scope, receive, send_with_log)
        except Exception:
            if not started:
                await self._log(scope, record, 500)
            raise
        finally:
            end_record(token)

    async def _log(self, scope: Scope, record: AccessRecord, status: int) -> bool:
        """기록했거나 기록 대상이 아니면 True, 기록에 실패하면 False"""
        spec = spec_of(scope.get("endpoint"))
        route = scope.get("route")
        if spec is None or route is None or record.actor_login_id is None:
            return True
        event = build_event(spec, record, route.path, "SUCCESS" if status < 400 else "FAILURE")
        try:
            await run_in_threadpool(_append, scope["app"].state.engine, event)
        except Exception:
            logger.exception(
                "self access log failed — response blocked (%s %s)", spec.action, route.path
            )
            return False
        return True


def _append(engine, event: dict) -> None:
    now = datetime.now(UTC)
    # 외부 출처와 같은 검증을 거친다 — 자체 기록이라고 형식 규칙을 비켜 가지 않게
    entry = validate_event(event, now, internal=True)
    if isinstance(entry, Rejection):
        raise ValueError(f"self access log rejected: {entry.code}")
    # 업무 트랜잭션과 무관한 새 트랜잭션, 같은 append 함수(해시체인·advisory lock)
    with engine.begin() as conn:
        entry["source_system_id"] = conn.execute(
            select(source_system.c.id).where(source_system.c.code == SOURCE)
        ).scalar_one()
        append_access_logs(conn, [entry], received_at=now)


async def _send_log_failure(send: Send) -> None:
    body = json.dumps(
        error_body("ACCESS_LOG_UNAVAILABLE", "access log could not be recorded")
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
