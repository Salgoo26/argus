"""접속기록 Agent 미들웨어 (architecture 3-2, api-spec 2절) — Servlet Filter 자리

흐름 (관리자 라우트만, CLAUDE.md 3절 #4):
1. 요청 시작: 기록지(AccessRecord)를 만들어 contextvars에 둔다 — 접속일시·접속지·검색조건 키 이름
2. 인증 의존성·핸들러가 기록지에 식별자·정보주체를 채운다
3. **응답 헤더를 보내기 직전**: 결과(SUCCESS/FAILURE)를 정해 outbox에 1건 적재
   - 업무 트랜잭션과 **별도 트랜잭션** (CLAUDE.md 3절 #6) — 업무가 실패·롤백돼도 시도는 남는다
   - 적재에 실패하면 원래 응답(CSV 등) 대신 500 — "기록할 수 없으면 내보내지 않는다"
     (fail-closed, implementation-log 2026-09-29 설계 변경 1)
4. 핸들러가 처리되지 않은 예외로 끝나면(응답 전) FAILURE로 적재한 뒤 예외를 그대로 올려보낸다

기록하지 않는 경우: 식별자가 없는 요청(미인증 401, 없는 ID로의 로그인 시도), 문패 대신 명시적 제외
표시가 붙은 라우트, 어느 라우트에도 매칭되지 않은 요청(404).

순수 ASGI로 작성 — 응답 시작 메시지를 가로채 적재 성공을 확인한 뒤에야 내보내야 하므로
(BaseHTTPMiddleware는 응답이 이미 만들어진 뒤에만 개입할 수 있다).
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.agent.client_ip import IPNetwork, resolve_client_ip
from app.agent.context import AccessRecord, end_record, start_record
from app.agent.decorators import SESSION_ACTIONS, AccessLogSpec, is_admin_path, spec_of
from app.errors import error_body
from app.outbox import enqueue

logger = logging.getLogger(__name__)

SUBJECT_IDS_LIMIT = 1000  # api-spec 2-2 — 넘으면 잘라 보내고 truncated=true, count는 전체
QUERY_KEYS_LIMIT = 50
_QUERY_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.[]-")


def query_keys(query_string: bytes) -> list[str]:
    """검색조건의 **키 이름만** — 값은 개인정보일 수 있어 버린다 (api-spec 2-2 최소수집)

    키 이름 형식이 아닌 것(값이 키 자리에 실려 온 경우 등)도 버린다 — Argus가 그 이벤트 전체를
    거부하지 않게, 그리고 개인정보가 키 모양으로 새지 않게.
    """
    keys: list[str] = []
    for key, _value in parse_qsl(query_string.decode("latin-1"), keep_blank_values=True):
        if 0 < len(key) <= 64 and set(key) <= _QUERY_KEY_CHARS and key not in keys:
            keys.append(key)
    return keys[:QUERY_KEYS_LIMIT]


def build_event(spec: AccessLogSpec, record: AccessRecord, route_path: str, result: str) -> dict:
    """api-spec 2-1 이벤트 1건"""
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        # 마이크로초까지 — Argus 탐지·정렬 기준 시각 (§2 3호 접속일시)
        "occurred_at": record.occurred_at.isoformat(timespec="microseconds"),
        "actor": {"login_id": record.actor_login_id},
        "client_ip": record.client_ip,
        "action": spec.action,
        "access_path": "APP",
        "data_category": spec.data_category,
        # 경로는 실제 URL이 아니라 라우트 템플릿(/admin/members/{member_id}) —
        # 경로 변수에 실린 값(검색어·이름 등)이 기록으로 새지 않게
        "request": {"method": record.method, "path": route_path, "query_keys": record.query_keys},
        "result": result,
        "context": record.context or {},
    }
    if spec.action not in SESSION_ACTIONS:
        # 정보주체를 기록하기 전에 실패했으면 건수 0 (2026-09-30 결정)
        ids = record.subject_ids or []
        count = record.subject_count if record.subject_count is not None else len(ids)
        event["subject"] = {
            "type": "MEMBER",
            "ids": ids[:SUBJECT_IDS_LIMIT],
            "count": max(count, len(ids)),
            "truncated": len(ids) > SUBJECT_IDS_LIMIT,
        }
    return event


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp, trusted_proxies: tuple[IPNetwork, ...] = ()) -> None:
        self.app = app
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_admin_path(scope["path"]):
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
                return  # 원래 응답 본문(CSV 등)은 내보내지 않는다
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
        """적재했거나 기록 대상이 아니면 True, 적재에 실패하면 False"""
        # 라우터가 매칭한 라우트를 scope에 적어 둔다 (starlette Router가 같은 scope dict를 갱신)
        spec = spec_of(scope.get("endpoint"))
        route = scope.get("route")
        if spec is None or route is None or record.actor_login_id is None:
            return True
        event = build_event(spec, record, route.path, "SUCCESS" if status < 400 else "FAILURE")
        engine = scope["app"].state.engine
        try:
            await run_in_threadpool(_enqueue_separately, engine, event)
        except Exception:
            # payload(회원 PK)는 로그에 남기지 않는다
            logger.exception(
                "access log enqueue failed — response blocked (%s %s)",
                spec.action,
                route.path,
            )
            return False
        return True


def _enqueue_separately(engine, event: dict) -> None:
    # 업무가 쓰던 커넥션·트랜잭션과 무관한 새 트랜잭션 — 업무 롤백에 휩쓸리지 않는다
    with engine.begin() as conn:
        enqueue(conn, "ACCESS_LOG", event)


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
