"""argus-worker — python -m app.worker [--once] (compose의 argus-worker 컨테이너)

argus-api와 같은 이미지·같은 앱 계정(argus_app)으로, 실행 명령만 다르다 (architecture 2절).
setting.detection_interval_min(기본 5분)마다 탐지 배치를 돌린다 — 주기는 매번 DB에서 다시 읽어
재시작 없이 설정 변경이 반영된다. 순찰마다 소명 기한 임박·초과 알림도 판정한다(v0.1 보강 F-3).

--once: 기다리지 않고 한 번만 순찰하고 끝낸다 (시연·E2E 테스트용). 실패하면 종료 코드 1.

왜 배치인가 (requirements LOG-03 "준실시간 배치 5분~1시간", 실시간 스트리밍은 인프라 부담으로 제외):
Argus는 요청을 막는 접근제어가 아니라 이미 일어난 행위를 보고 소명을 받는 탐지 통제다.
소명은 사람 속도로 진행되고, 집계형 룰·늦게 도착한 기록·실패 재처리가 모두 배치 구조에 자연스럽다.
"""

import argparse
import logging
import signal
import sys
import threading

from sqlalchemy import Engine, create_engine, select

from app.config import Settings
from app.detection.batch import MAX_LOGS, run_batch
from app.models import setting
from app.notifications.events import check_due
from app.notifications.webpush import VapidKeys, dispatch_pending, vapid_from

logger = logging.getLogger("worker")

DEFAULT_INTERVAL_MIN = 5


def interval_minutes(engine: Engine) -> int:
    with engine.connect() as conn:
        value = conn.execute(
            select(setting.c.value).where(setting.c.key == "detection_interval_min")
        ).scalar_one_or_none()
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    logger.warning("invalid detection_interval_min=%r — using %d", value, DEFAULT_INTERVAL_MIN)
    return DEFAULT_INTERVAL_MIN


def _check_due(engine: Engine, vapid: VapidKeys | None = None) -> None:
    """소명 기한 임박·초과 알림 (F-3) + 웹 푸시 발송 (F-4) — 실패해도 탐지 순찰은 계속한다"""
    try:
        created = check_due(engine)
        if created:
            logger.info("due notifications created: %d", created)
    except Exception:
        logger.exception("due check failed")
    try:
        sent = dispatch_pending(engine, vapid)
        if sent:
            logger.info("web push sent: %d", sent)
    except Exception:
        logger.exception("web push dispatch failed")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="순찰을 한 번만 실행하고 종료")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s [worker] %(message)s")
    settings = Settings()
    engine = create_engine(settings.database_url(), pool_pre_ping=True)
    vapid = vapid_from(settings)  # 키가 없으면 None — 웹 푸시만 꺼진다

    if args.once:
        result = run_batch(engine)
        _check_due(engine, vapid)
        engine.dispose()
        if result is None:
            print("skipped: another detection batch is running", file=sys.stderr)
            return 1
        print(
            f"batch #{result.run_id} {result.status}: range ({result.from_id}..{result.to_id}] "
            f"processed={result.processed} detected={result.detected}"
        )
        return 0 if result.status == "SUCCESS" else 1

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # docker stop → 진행 중인 순찰을 마치고 종료

    logger.info("started")
    while not stop.is_set():
        try:
            result = run_batch(engine)
            _check_due(engine, vapid)
            # 한 번에 처리할 수 있는 만큼 꽉 채웠으면 밀려 있다는 뜻 — 쉬지 않고 다음 순찰
            backlog = result is not None and result.processed == MAX_LOGS
            wait_sec = 0 if backlog else interval_minutes(engine) * 60
        except Exception:
            # DB 재기동 등 — 죽지 않고 1분 뒤 다시 (실행 이력·책갈피는 DB에 남아 있다)
            logger.exception("detection cycle failed")
            wait_sec = 60
        stop.wait(wait_sec)
    engine.dispose()
    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
