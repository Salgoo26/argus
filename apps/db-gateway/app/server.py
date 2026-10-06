"""DB 툴 연결 처리 — TLS → 토큰 인증 → 공용 계정으로 중계 (architecture 3-4)

    [DB 툴] ──(사용자 = 플랫폼 아이디, 비밀번호 = DB 접속 토큰, TLS)──▶ [게이트웨이]
            ──(공용 계정)──▶ [platform-db]

연결 하나의 흐름:
1. TLS가 아닌 연결은 인증 전에 거부 — 토큰이 평문으로 흐르지 않게 (§7④)
2. 평문 비밀번호 요청(AuthenticationCleartextPassword)으로 토큰을 받는다 — TLS 안이라 암호화됨
3. 토큰(서명·만료·용도·주인) + 계정 상태(operator) 확인
4. 공용 계정으로 platform-db에 연결 (사용자 연결 1개 = DB 연결 1개)
5. LOGIN 접속기록을 원문과 함께 남긴 **뒤에야** DB 툴에 "로그인 성공"을 보낸다
   — 기록할 수 없으면 거부(fail-closed)
6. 이후 메시지를 양방향으로 중계하고, 토큰 만료 시각에 연결을 끊는다

접속기록(api-spec 2-2·2-4 "DB 직접 접근"):
- 인증 성공·실패 모두 LOGIN으로 남긴다.
  단 **존재하지 않는 아이디는 Argus에도 원문 저장소에도 남기지 않는다**
  (아이디 칸에 잘못 입력된 비밀번호가 영구 저장되지 않게 — 3티어 관리자 로그인과 같은 규칙)
- 원문에는 접속 정보만 — 토큰 값(비밀번호)은 어디에도 남기지 않는다
- 문장(SQL) 기록은 session.py — 완료 신호를 넘기기 전에 기록한다
"""

import asyncio
import logging
import struct
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app import protocol as pg
from app import upstream
from app.auth import OperatorStatus, TokenCheck, check_token
from app.catalog import Catalog
from app.session import Session
from app.store import Store

logger = logging.getLogger("gateway")

STARTUP_TIMEOUT_SEC = 30  # 인증을 끝내지 않고 연결만 붙잡는 클라이언트 정리
# 플랫폼 DB로 넘기는 시작 매개변수 — 허용 목록만. options·replication 등은 넘기지 않는다
FORWARDED_PARAMS = (
    "application_name",
    "client_encoding",
    "DateStyle",
    "TimeZone",
    "IntervalStyle",
    "extra_float_digits",
    "search_path",
)

# DB 툴 화면에 보이는 문구 — 입력값(토큰)을 되풀이하지 않는다
MESSAGES = {
    "TLS_REQUIRED": ("28000", "TLS required — set sslmode=require"),
    "INVALID_TOKEN": ("28P01", "invalid DB access token"),
    "TOKEN_EXPIRED": ("28P01", "DB access token expired — issue a new one in the admin console"),
    "ACCOUNT_DISABLED": ("28000", "account is disabled"),
    "ACCOUNT_LOCKED": ("28000", "account is locked"),
    "DATABASE_NOT_ALLOWED": ("3D000", "only the platform database is available"),
    "AUTH_UNAVAILABLE": ("08006", "authentication is temporarily unavailable"),
    "UPSTREAM_UNAVAILABLE": ("08006", "platform database is unavailable"),
    "ACCESS_LOG_UNAVAILABLE": ("08006", "access log unavailable — connection refused"),
}


@dataclass(frozen=True)
class UpstreamConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


OperatorLookup = Callable[[str], Awaitable[OperatorStatus | None]]


class Gateway:
    def __init__(
        self,
        *,
        store: Store,
        ssl_context,
        token_key: bytes,
        upstream_config: UpstreamConfig,
        operator_lookup: OperatorLookup,
        catalog: Catalog,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.ssl_context = ssl_context
        self.token_key = token_key
        self.upstream = upstream_config
        self.operator_lookup = operator_lookup
        self.catalog = catalog
        self.clock = clock
        self.backend_keys: set[bytes] = set()  # 살아 있는 중계 연결 — 취소 요청 검증용

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        client_ip = peer[0] if peer else None
        try:
            async with asyncio.timeout(STARTUP_TIMEOUT_SEC):
                session = await self._start(reader, writer, client_ip)
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, pg.ProtocolError):
            session = None
        except Exception:
            logger.exception("connection setup failed")
            session = None
        if session is None:
            writer.close()
            return
        await self._relay(reader, writer, *session)

    # ── 1~5. TLS·인증·DB 연결·LOGIN 기록 ──

    async def _start(self, reader, writer, client_ip):
        code, payload = await pg.read_startup(reader)
        if code == pg.CANCEL_REQUEST:
            await self._forward_cancel(payload)
            return None
        if code == pg.GSSENC_REQUEST:
            writer.write(b"N")
            await writer.drain()
            code, payload = await pg.read_startup(reader)
        if code != pg.SSL_REQUEST:
            # 인증 전 거부 — 누구인지 확인하기 전이라 기록 대상이 아니다
            await self._refuse(writer, "TLS_REQUIRED")
            return None
        writer.write(b"S")
        await writer.drain()
        await writer.start_tls(self.ssl_context)
        tls = writer.get_extra_info("ssl_object")

        code, payload = await pg.read_startup(reader)
        if code == pg.CANCEL_REQUEST:
            await self._forward_cancel(payload)
            return None
        if code >> 16 != 3:
            raise pg.ProtocolError("unsupported protocol version")
        if code & 0xFFFF:
            writer.write(pg.negotiate_protocol_version(0))  # 3.0으로 맞춘다
        params = pg.parse_params(payload)
        login_id = params.get("user", "")
        database = params.get("database") or login_id

        writer.write(pg.auth_request(pg.AUTH_CLEARTEXT))
        await writer.drain()
        tag, body = await pg.read_message(reader)
        if tag != b"p" or len(body) > pg.MAX_PASSWORD_LENGTH:
            raise pg.ProtocolError("expected password message")
        token = body.rstrip(b"\0").decode("utf-8", "replace")

        try:
            status = await self.operator_lookup(login_id)
        except Exception:
            # 계정 상태를 확인할 수 없으면 거부 (fail-closed, architecture 3-4 "계정 상태")
            logger.exception("operator lookup failed")
            await self._refuse(writer, "AUTH_UNAVAILABLE")
            return None
        if status is None:
            # 존재하지 않는 아이디 — 어디에도 남기지 않고, 토큰 오류와 같은 응답(계정 열거 방지)
            await self._refuse(writer, "INVALID_TOKEN")
            return None

        login = _LoginAttempt(self.clock(), login_id, client_ip, tls, params)
        check = check_token(token, self.token_key, login_id, login.occurred_at)
        failure = (
            check.reason
            or status.blocked_reason
            or (None if database == self.upstream.database else "DATABASE_NOT_ALLOWED")
        )
        if failure:
            await self._record_login(login, check, failure)
            await self._refuse(writer, failure)
            return None

        try:
            conn = await upstream.connect(
                self.upstream.host,
                self.upstream.port,
                self.upstream.user,
                self.upstream.password,
                self.upstream.database,
                {k: params[k] for k in FORWARDED_PARAMS if k in params},
            )
        except (upstream.UpstreamError, asyncio.IncompleteReadError, ConnectionError) as error:
            logger.error("upstream connect failed: %s", error)
            await self._record_login(login, check, "UPSTREAM_UNAVAILABLE")
            await self._refuse(writer, "UPSTREAM_UNAVAILABLE")
            return None

        if not await self._record_login(login, check, None):
            conn.writer.close()
            await self._refuse(writer, "ACCESS_LOG_UNAVAILABLE")
            return None

        writer.write(pg.auth_request(pg.AUTH_OK))
        for tag, body in conn.greeting:
            writer.write(pg.message(tag, body))
        await writer.drain()
        logger.info("session opened: login_id=%s token_id=%s", login_id, check.token_id)
        return conn, check, login

    async def _record_login(self, login, check: TokenCheck, failure: str | None) -> bool:
        raw, event = login.records(self.upstream.user, check, failure)
        try:
            await asyncio.to_thread(self.store.record, raw, event)
        except Exception:
            # 기록할 수 없으면 연결을 열지 않는다 (fail-closed — 3티어 3-2와 같은 원칙)
            logger.exception("failed to record LOGIN")
            return False
        return True

    async def _refuse(self, writer, reason: str) -> None:
        code, text = MESSAGES.get(reason, MESSAGES["INVALID_TOKEN"])
        if reason in ("TOKEN_OWNER_MISMATCH", "WRONG_AUDIENCE"):
            code, text = MESSAGES["INVALID_TOKEN"]
        writer.write(pg.error_response(code, text))
        try:
            await writer.drain()
        except ConnectionError:
            pass

    async def _forward_cancel(self, payload: bytes) -> None:
        """실행 중인 쿼리 취소 — 게이트웨이가 연 연결의 키일 때만 platform-db로 전달"""
        if payload[:8] not in self.backend_keys:
            return
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection(self.upstream.host, self.upstream.port), 5
            )
            w.write(struct.pack("!II", 16, pg.CANCEL_REQUEST) + payload[:8])
            await w.drain()
            w.close()
        except (OSError, TimeoutError):
            logger.warning("cancel request could not be forwarded")

    # ── 6. 중계 ──

    async def _relay(
        self, reader, writer, conn: upstream.Upstream, check: TokenCheck, login: "_LoginAttempt"
    ) -> None:
        if conn.backend_key:
            self.backend_keys.add(conn.backend_key[:8])
        session = Session(
            store=self.store,
            catalog=self.catalog,
            login_id=login.login_id,
            client_ip=login.client_ip,
            token_id=check.token_id,
            db_user=self.upstream.user,
            clock=self.clock,
        )
        tasks = {
            asyncio.create_task(_client_to_db(session, reader, conn.writer, writer)),
            asyncio.create_task(_db_to_client(session, conn.reader, writer)),
            asyncio.create_task(self._expire(writer, check)),
        }
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            if conn.backend_key:
                self.backend_keys.discard(conn.backend_key[:8])
            conn.writer.close()
            writer.close()

    async def _expire(self, writer, check: TokenCheck) -> None:
        """토큰 만료 시각에 연결을 끊는다 — 1시간 토큰으로 연결을 무기한 붙잡지 못하게"""
        remaining = (check.expires_at - self.clock()).total_seconds()
        await asyncio.sleep(max(remaining, 0))
        await _send(
            writer, pg.error_response("57P01", "DB access token expired — connection closed")
        )
        logger.info("session closed at token expiry: token_id=%s", check.token_id)


async def _send(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(data)
    try:
        await writer.drain()
    except ConnectionError:
        pass


async def _client_to_db(session: Session, src, db_writer, client_writer) -> None:
    try:
        while True:
            tag, body = await pg.read_message(src)
            if session.from_client(tag, body):
                # 기록할 수 없는 요청(fastpath 함수 호출) — DB로 넘기지 않고 연결을 끊는다
                await _send(
                    client_writer, pg.error_response("0A000", "function call not supported")
                )
                return
            db_writer.write(pg.message(tag, body))
            await db_writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, pg.ProtocolError):
        return


async def _db_to_client(session: Session, src, client_writer) -> None:
    try:
        while True:
            tag, body = await pg.read_message(src)
            # 완료·오류 신호는 기록을 마친 뒤에만 넘긴다
            # — 기록하지 못하면 오류로 바꾸고 끊는다 (fail-closed)
            if not await session.from_server(tag, body):
                await _send(client_writer, pg.error_response(*MESSAGES["ACCESS_LOG_UNAVAILABLE"]))
                return
            client_writer.write(pg.message(tag, body))
            await client_writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, pg.ProtocolError):
        return


@dataclass(frozen=True)
class _LoginAttempt:
    occurred_at: datetime
    login_id: str
    client_ip: str | None
    tls: object
    params: dict[str, str]

    def records(self, db_user: str, check: TokenCheck, failure: str | None) -> tuple[dict, dict]:
        event_id = str(uuid.uuid4())
        occurred_at = self.occurred_at.isoformat()
        result = "FAILURE" if failure else "SUCCESS"
        raw = {
            "kind": "LOGIN",
            "event_id": event_id,
            "occurred_at": occurred_at,
            "login_id": self.login_id,
            "client_ip": self.client_ip,
            "tls": {"version": self.tls.version(), "cipher": self.tls.cipher()[0]},
            "params": {
                k: self.params[k] for k in ("database", "application_name") if k in self.params
            },
            "result": result,
        }
        context = {"db_user": db_user}
        if failure:
            raw["failure_reason"] = failure
        if check.token_id:
            raw["token_id"] = check.token_id
            context["token_id"] = check.token_id
        event = {
            "event_id": event_id,
            "occurred_at": occurred_at,
            "actor": {"login_id": self.login_id},
            "client_ip": self.client_ip,
            "action": "LOGIN",
            "access_path": "DB",
            "data_category": "NONE",
            "result": result,
            "context": context,
        }
        return raw, event
