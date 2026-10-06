"""전송 루프 — 게이트웨이 버퍼(store.outbox) → Argus 수집 API (architecture 3-4 "세부 결정")

플랫폼 relay(apps/platform-api/app/relay.py)의 규칙을 그대로 옮겼다.
게이트웨이는 보안 경계라 플랫폼과
이미지를 나누므로 코드를 import하지 않고 복사했다 — 규칙을 바꿀 때는 두 곳을 함께 고친다.

- 출처는 PLATFORM(같은 HMAC 키) → Argus의 취급자 매칭 (source_system, login_id)이 3티어와 같다
- 200: 건별 판정 — accepted·duplicate는 삭제, rejected는 DEAD + 오류 로그
- 400: 배치 전체 DEAD / 413: 반으로 나눠 재전송
- 401·429·5xx·네트워크 오류·그 밖: PENDING 유지, 지수 백오프 1분 → 최대 1시간, 무기한
접속기록은 스스로 버리지 않는다 (CLAUDE.md 3절 #7).
로그에는 건수·오류 코드만 — payload는 남기지 않는다.
"""

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.store import PendingEvent, Store, canonical_json

logger = logging.getLogger("gateway.sender")

SOURCE = "PLATFORM"
PATH = "/ingest/v1/access-logs"
BATCH_SIZE = 100  # api-spec 1-1
POLL_INTERVAL_SEC = 2
BASE_BACKOFF_SEC = 60
MAX_BACKOFF_SEC = 3600
HTTP_TIMEOUT_SEC = 10


@dataclass(frozen=True)
class HttpResult:
    status: int | None  # None = 연결 실패·타임아웃 (응답 자체가 없음)
    body: bytes = b""


Poster = Callable[[str, dict[str, str], bytes], HttpResult]


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """argus-api ingest/auth.py의 검증과 같은 규칙: v1= + hex(HMAC-SHA256(secret, "{ts}.{body}"))"""
    mac = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def backoff_seconds(attempts: int) -> int:
    """attempts번째 실패 뒤의 대기 — 1분, 2분, 4분 … 최대 1시간"""
    return min(BASE_BACKOFF_SEC * 2 ** max(attempts - 1, 0), MAX_BACKOFF_SEC)


def urllib_post(url: str, headers: dict[str, str], body: bytes) -> HttpResult:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 — 스킴은 설정 단계에서 http(s)로 제한
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:  # noqa: S310
            return HttpResult(response.status, response.read())
    except urllib.error.HTTPError as error:
        return HttpResult(error.code, error.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return HttpResult(None)


def _error_code(result: HttpResult) -> str:
    if result.status is None:
        return "NETWORK_ERROR"
    try:
        return str(json.loads(result.body)["error"]["code"])[:64]
    except (ValueError, KeyError, TypeError):
        return f"HTTP_{result.status}"


class Sender:
    def __init__(
        self,
        store: Store,
        base_url: str,
        secret: bytes,
        poster: Poster = urllib_post,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.url = base_url + PATH
        self.secret = secret
        self.poster = poster
        self.clock = clock

    def run_once(self) -> int:
        # 보낼 차례는 실제 시각으로 판단한다 — clock은 서명 시각(X-Argus-Timestamp)용 (relay와 같음)
        events = self.store.due(BATCH_SIZE, time.time())
        if events:
            self._deliver(events)
        return len(events)

    def _deliver(self, events: Sequence[PendingEvent]) -> None:
        body = ('{"events":[' + ",".join(canonical_json(e.payload) for e in events) + "]}").encode()
        timestamp = str(int(self.clock()))
        result = self.poster(
            self.url,
            {
                "Content-Type": "application/json; charset=utf-8",
                "X-Argus-Source": SOURCE,
                "X-Argus-Timestamp": timestamp,
                "X-Argus-Signature": sign(self.secret, timestamp, body),
            },
            body,
        )

        if result.status == 200:
            self._apply_results(events, result)
        elif result.status == 400:
            code = _error_code(result)
            logger.error("batch rejected as a whole (400 %s): %d → DEAD", code, len(events))
            self.store.mark_dead([e.id for e in events], f"400 {code}")
        elif result.status == 413 and len(events) > 1:
            half = len(events) // 2
            logger.warning("batch too large (413): splitting %d", len(events))
            self._deliver(events[:half])
            self._deliver(events[half:])
        else:
            code = _error_code(result)
            transient = result.status is None or result.status == 429 or result.status >= 500
            level = logging.WARNING if transient else logging.ERROR
            logger.log(level, "batch not delivered (%s): %d kept PENDING", code, len(events))
            self._retry_later(events, code)

    def _apply_results(self, events: Sequence[PendingEvent], result: HttpResult) -> None:
        try:
            response = json.loads(result.body)
            rejected = {
                str(item.get("event_id")): f"{item.get('code')}: {item.get('message')}"[:500]
                for item in response["rejected"]
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            # 무엇이 저장됐는지 모르므로 지우지 않고 다시 보낸다 (중복은 Argus가 걸러낸다)
            logger.error("unreadable 200 response, kept PENDING")
            self._retry_later(events, "UNREADABLE_RESPONSE")
            return

        dead = [e for e in events if e.event_id in rejected]
        done = [e.id for e in events if e.event_id not in rejected]
        for event in dead:
            self.store.mark_dead([event.id], rejected[event.event_id])
        if done:
            self.store.delete(done)
        logger.info(
            "sent: accepted=%s duplicates=%s rejected=%d deleted=%d",
            response.get("accepted"),
            response.get("duplicates"),
            len(dead),
            len(done),
        )
        if dead:
            logger.error("%d event(s) rejected by Argus → DEAD", len(dead))

    def _retry_later(self, events: Sequence[PendingEvent], error: str) -> None:
        self.store.retry_later(events, error, [backoff_seconds(e.attempts + 1) for e in events])
