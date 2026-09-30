"""outbox relay — python -m app.relay (compose의 platform-relay 컨테이너)

outbox(발송 대기함)를 2초마다 비워 Argus로 보낸다 (architecture 3-3, api-spec 1·4절).
"보낸다 → Argus가 받았다고 확인한다 → 그때 지운다" 순서라, 어디서 죽어도 유실이 없다.
대신 같은 건이 두 번 갈 수 있고(at-least-once), Argus가 event_id·last_event_at으로 중복을 걸러
결과적으로 정확히 한 번 저장된다.

응답별 처리 (api-spec 1-5):
- 200: 건별 판정 — accepted·duplicate는 삭제, rejected는 DEAD(재시도 안 함) + 오류 로그
- 400: 요청 전체가 파싱 불가 → 배치 전체 DEAD
- 401: PENDING 유지 + 오류 로그. 백오프 간격으로만 재확인해 사람이 키·시각을 고치면 저절로 재개
  (implementation-log 2026-09-29 설계 변경 5)
- 413: 배치를 반으로 쪼개 재전송
- 429·5xx·네트워크 오류·그 밖의 응답: PENDING, 지수 백오프 1분 → 2 → 4 … 최대 1시간, 무기한
접속기록은 스스로 버리지 않는다 (CLAUDE.md 3절 #7) — DEAD는 Argus가 명시적으로 거부한 경우뿐.

로그에는 건수·오류 코드만 남긴다. payload(회원 PK)는 남기지 않는다.
"""

import hashlib
import hmac
import json
import logging
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, delete, select, update

from app.config import Settings
from app.models import outbox

logger = logging.getLogger("relay")

SOURCE = "PLATFORM"
TOPIC_PATHS = {
    "HANDLER": "/ingest/v1/handler-events",  # 취급자 동기화(②) 먼저 — 명부가 기록보다 앞서 있게
    "ACCESS_LOG": "/ingest/v1/access-logs",  # 접속기록 수집(①)
}
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


class Relay:
    def __init__(
        self,
        engine: Engine,
        base_url: str,
        secret: bytes,
        poster: Poster = urllib_post,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self.base_url = base_url
        self.secret = secret
        self.poster = poster
        self.clock = clock

    def run_once(self) -> int:
        """topic마다 보낼 차례인 건을 한 배치씩 처리하고, 가장 많이 꺼낸 배치 크기를 돌려준다."""
        return max(self._process_topic(topic) for topic in TOPIC_PATHS)

    def _process_topic(self, topic: str) -> int:
        with self.engine.begin() as conn:
            # SKIP LOCKED: relay가 둘 이상 떠도 같은 건을 동시에 집지 않는다.
            # 락은 전송·결과 반영이 끝나고 트랜잭션이 닫힐 때 풀린다
            rows = conn.execute(
                select(outbox.c.id, outbox.c.event_id, outbox.c.payload, outbox.c.attempts)
                .where(
                    outbox.c.status == "PENDING",
                    outbox.c.topic == topic,
                    outbox.c.next_retry_at <= datetime.now(UTC),
                )
                .order_by(outbox.c.created_at, outbox.c.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            ).all()
            if rows:
                self._deliver(conn, topic, rows)
            return len(rows)

    def _deliver(self, conn: Connection, topic: str, rows: Sequence[Any]) -> None:
        body = json.dumps(
            {"events": [row.payload for row in rows]}, ensure_ascii=False, separators=(",", ":")
        ).encode()
        timestamp = str(int(self.clock()))
        result = self.poster(
            self.base_url + TOPIC_PATHS[topic],
            {
                "Content-Type": "application/json; charset=utf-8",
                "X-Argus-Source": SOURCE,
                "X-Argus-Timestamp": timestamp,
                "X-Argus-Signature": sign(self.secret, timestamp, body),
            },
            body,
        )

        if result.status == 200:
            self._apply_results(conn, topic, rows, result)
        elif result.status == 400:
            code = _error_code(result)
            logger.error("%s batch rejected as a whole (400 %s): %d → DEAD", topic, code, len(rows))
            self._mark_dead(conn, [row.id for row in rows], f"400 {code}")
        elif result.status == 413 and len(rows) > 1:
            half = len(rows) // 2
            logger.warning("%s batch too large (413): splitting %d", topic, len(rows))
            self._deliver(conn, topic, rows[:half])
            self._deliver(conn, topic, rows[half:])
        else:
            code = _error_code(result)
            transient = result.status is None or result.status == 429 or result.status >= 500
            # 일시 장애는 WARNING. 401·404·413(1건) 등 설정 문제는 사람이 고쳐야 하므로 ERROR
            level = logging.WARNING if transient else logging.ERROR
            logger.log(
                level, "%s batch not delivered (%s): %d kept PENDING", topic, code, len(rows)
            )
            self._retry_later(conn, rows, code)

    def _apply_results(self, conn: Connection, topic: str, rows: Sequence[Any], result: HttpResult):
        try:
            response = json.loads(result.body)
            rejected = {
                str(item.get("event_id")): f"{item.get('code')}: {item.get('message')}"[:500]
                for item in response["rejected"]
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            # 200인데 본문을 해석할 수 없음 — 무엇이 저장됐는지 모르므로 지우지 않고 다시 보낸다
            # (중복은 Argus가 걸러낸다)
            logger.error("%s: unreadable 200 response, kept PENDING", topic)
            self._retry_later(conn, rows, "UNREADABLE_RESPONSE")
            return

        dead = [row for row in rows if str(row.event_id) in rejected]
        done = [row.id for row in rows if str(row.event_id) not in rejected]
        for row in dead:
            self._mark_dead(conn, [row.id], rejected[str(row.event_id)])
        if done:
            conn.execute(delete(outbox).where(outbox.c.id.in_(done)))
        logger.info(
            "%s sent: accepted=%s duplicates=%s rejected=%d deleted=%d",
            topic,
            response.get("accepted"),
            response.get("duplicates"),
            len(dead),
            len(done),
        )
        if dead:
            # 조용히 버리지 않는다 — 사람이 확인하도록 오류로 남긴다 (api-spec 1-4)
            logger.error("%s: %d event(s) rejected by Argus → DEAD", topic, len(dead))

    def _mark_dead(self, conn: Connection, ids: list[int], error: str) -> None:
        conn.execute(
            update(outbox)
            .where(outbox.c.id.in_(ids))
            .values(status="DEAD", attempts=outbox.c.attempts + 1, last_error=error)
        )

    def _retry_later(self, conn: Connection, rows: Sequence[Any], error: str) -> None:
        now = datetime.now(UTC)
        for row in rows:
            attempts = row.attempts + 1
            conn.execute(
                update(outbox)
                .where(outbox.c.id == row.id)
                .values(
                    attempts=attempts,
                    next_retry_at=now + timedelta(seconds=backoff_seconds(attempts)),
                    last_error=error,
                )
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [relay] %(message)s")
    settings = Settings()
    base_url, secret = settings.require_argus_ingest()
    engine = create_engine(settings.database_url(), pool_pre_ping=True)
    relay = Relay(engine, base_url, secret)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # docker stop → 진행 중인 배치를 마치고 종료

    logger.info("started — polling every %ds, target %s", POLL_INTERVAL_SEC, base_url)
    while not stop.is_set():
        try:
            busy = relay.run_once() == BATCH_SIZE
        except Exception:
            # DB 재기동 등 — 죽지 않고 다음 주기에 다시 시도 (outbox는 DB에 남아 있다)
            logger.exception("relay cycle failed")
            busy = False
        if not busy:  # 한 배치를 꽉 채웠으면 쉬지 않고 바로 다음 배치
            stop.wait(POLL_INTERVAL_SEC)
    engine.dispose()
    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
