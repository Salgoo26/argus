"""상태 진행·쇼핑몰 통지 루프 — API 프로세스 안의 스레드 하나

작은 외부 서비스 흉내라 컨테이너를 따로 두지 않는다.

한 번(tick)에 하는 일:
1. auto_progress가 on이면, 각 상태에 setting의 시간만큼 머문 접수 건을 다음 상태로 넘긴다
2. 마지막 통지 이후 상태가 바뀐 건(앱이 바꿨든 DB에서 직접 바꿨든)을 쇼핑몰에 서명해서 통지한다.
   실패하면 5초 → 10초 → … 최대 5분 간격으로 다시 보낸다
"""

import hashlib
import hmac
import json
import logging
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from app.db import NEXT_STATUS, mark_source
from app.shipments import shipment_json

logger = logging.getLogger("courier.worker")

NOTIFY_BATCH = 50
HTTP_TIMEOUT_SEC = 5
MAX_BACKOFF_SEC = 300

# (url, headers, body) → HTTP 상태 코드(연결 실패면 None)
Sender = Callable[[str, dict[str, str], bytes], int | None]


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """쇼핑몰이 검증하는 서명: v1= + hex(HMAC-SHA256(secret, "{timestamp}.{body}"))"""
    return "v1=" + hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def urllib_send(url: str, headers: dict[str, str], body: bytes) -> int | None:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 — 스킴은 설정에서 http(s)로 제한
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def advance_due(conn) -> int:
    settings = dict(conn.execute(text("SELECT key, value FROM setting")).all())
    if settings.get("auto_progress", "on").lower() != "on":
        return 0
    mark_source(conn, "AUTO")
    moved = 0
    for status, nxt in NEXT_STATUS.items():
        raw = settings.get(f"delay.{status}", "")
        if not raw.isdigit():
            continue  # 잘못된 값이면 그 단계는 자동으로 넘기지 않는다
        moved += conn.execute(
            text(
                "UPDATE shipment SET status = :next WHERE status = :status"
                " AND status_changed_at <= now() - make_interval(secs => :delay)"
            ),
            {"next": nxt, "status": status, "delay": int(raw)},
        ).rowcount
    return moved


def notify_pending(engine: Engine, url: str, secret: bytes, company: str, send: Sender) -> int:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT * FROM shipment WHERE notified_status IS DISTINCT FROM status"
                    " AND next_notify_at <= now() ORDER BY status_changed_at LIMIT :n"
                ),
                {"n": NOTIFY_BATCH},
            )
            .mappings()
            .all()
        )
    sent = 0
    for row in rows:
        body = json.dumps(
            {
                "eventId": str(uuid.uuid4()),
                "type": "SHIPMENT_STATUS_CHANGED",
                "shipment": shipment_json(row, company),
            },
            ensure_ascii=False,
        ).encode()
        timestamp = str(int(datetime.now(UTC).timestamp()))
        status = send(
            url,
            {
                "Content-Type": "application/json",
                "X-Courier-Timestamp": timestamp,
                "X-Courier-Signature": sign(secret, timestamp, body),
            },
            body,
        )
        with engine.begin() as conn:
            if status is not None and 200 <= status < 300:
                # 보내는 사이 상태가 또 바뀌었으면 그대로 두어 다음 루프에서 새 상태를 보낸다
                conn.execute(
                    text(
                        "UPDATE shipment SET notified_status = :s, notify_attempts = 0"
                        " WHERE shipment_id = :id AND status = :s"
                    ),
                    {"s": row["status"], "id": row["shipment_id"]},
                )
                sent += 1
            else:
                attempts = row["notify_attempts"] + 1
                delay = min(5 * 2 ** (attempts - 1), MAX_BACKOFF_SEC)
                conn.execute(
                    text(
                        "UPDATE shipment SET notify_attempts = :a,"
                        " next_notify_at = now() + make_interval(secs => :d)"
                        " WHERE shipment_id = :id"
                    ),
                    {"a": attempts, "d": delay, "id": row["shipment_id"]},
                )
                logger.warning(
                    "notify failed shipment=%s status=%s attempts=%d",
                    row["shipment_id"],
                    status,
                    attempts,
                )
    return sent


def tick(engine: Engine, url: str, secret: bytes, company: str, send: Sender) -> None:
    with engine.begin() as conn:
        advance_due(conn)
    notify_pending(engine, url, secret, company, send)


def start(interval: float, run: Callable[[], None]) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                run()
            except Exception:  # 루프가 죽으면 상태 진행이 멈춘다 — 기록하고 계속
                logger.exception("courier tick failed")
            stop.wait(interval)

    threading.Thread(target=loop, name="courier-worker", daemon=True).start()
    return stop
