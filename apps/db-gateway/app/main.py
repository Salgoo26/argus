"""db-gateway 진입점 — python -m app.main (compose의 db-gateway 컨테이너)

한 프로세스에서 두 가지를 돌린다:
- 게이트웨이 서버(asyncio): DB 툴 연결을 받아 중계
- 전송 루프(스레드): 버퍼의 접속기록을 Argus로
  (Argus가 꺼져 있어도 중계는 계속되고, 기록은 쌓였다가 나간다)
"""

import asyncio
import logging
import re
import signal
import sys
import threading

from psycopg.conninfo import make_conninfo

from app.auth import fetch_operator_status
from app.catalog import Catalog
from app.config import Settings
from app.registry import Registry
from app.registry_sync import RegistrySync, read_schema, run_registry_sync
from app.sender import BATCH_SIZE, POLL_INTERVAL_SEC, Sender
from app.server import Gateway, UpstreamConfig
from app.store import Store
from app.tls import ensure_self_signed, server_context

logger = logging.getLogger("gateway")

_DB_USER = re.compile(r"^[A-Za-z0-9_]{1,63}$")  # api-spec 2-2 context.db_user 형식


def run_sender(sender: Sender, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            busy = sender.run_once() == BATCH_SIZE
        except Exception:
            logger.exception("sender cycle failed")
            busy = False
        if not busy:
            stop.wait(POLL_INTERVAL_SEC)


async def serve(settings: Settings, stop: threading.Event) -> None:
    token_key = settings.require_token_key()
    base_url, secret = settings.require_argus_ingest()
    if not _DB_USER.fullmatch(settings.upstream_user):
        # Argus가 모든 기록을 거부하게 되는 설정 — 기동 단계에서 막는다
        raise ValueError("GATEWAY_UPSTREAM_USER must match [A-Za-z0-9_]{1,63}")

    store = Store(settings.data_dir / "gateway.sqlite3")
    if settings.tls_cert_file and settings.tls_key_file:
        cert, key = settings.tls_cert_file, settings.tls_key_file
    else:
        cert, key = ensure_self_signed(settings.data_dir / "tls")

    # 마지막으로 받은 등록부 — 없으면 고정 표(builtin)로 시작한다
    registry = Registry(settings.data_dir / "registry.json")

    password = settings.upstream_password.get_secret_value()
    conninfo = make_conninfo(
        host=settings.upstream_host,
        port=settings.upstream_port,
        dbname=settings.upstream_db,
        user=settings.upstream_user,
        password=password,
        application_name="db-gateway",
    )
    gateway = Gateway(
        store=store,
        ssl_context=server_context(cert, key),
        token_key=token_key,
        upstream_config=UpstreamConfig(
            settings.upstream_host,
            settings.upstream_port,
            settings.upstream_db,
            settings.upstream_user,
            password,
        ),
        operator_lookup=lambda login_id: fetch_operator_status(conninfo, login_id),
        catalog=Catalog(conninfo),
        registry=registry,
    )

    sender_thread = threading.Thread(
        target=run_sender, args=(Sender(store, base_url, secret), stop), name="sender"
    )
    sender_thread.start()
    # 보호 대상 등록부 받기·DB 구조 목록 보내기 (v0.1 보강 N-2·N-3)
    sync = RegistrySync(registry, base_url, secret, lambda: read_schema(conninfo))
    registry_thread = threading.Thread(
        target=run_registry_sync, args=(sync, stop), name="registry", daemon=True
    )
    registry_thread.start()
    server = await asyncio.start_server(gateway.handle, settings.listen_host, settings.listen_port)
    logger.info(
        "listening on %s:%d, sending to %s", settings.listen_host, settings.listen_port, base_url
    )
    try:
        await asyncio.to_thread(stop.wait)
    finally:
        server.close()
        await server.wait_closed()
        stop.set()
        sender_thread.join()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [gateway] %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    asyncio.run(serve(Settings(), stop))
    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
