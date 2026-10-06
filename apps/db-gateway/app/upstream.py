"""플랫폼 DB 쪽 연결 — 공용 계정으로 로그인하고, 로그인 직후 서버가 보내는 메시지를 모은다

사용자 연결 1개 = DB 연결 1개 (커넥션 풀링 금지 — 풀링하면 SQL과 사람의 대응이 깨진다,
architecture 3-4).
게이트웨이 ↔ platform-db는 도커 내부망 + SCRAM-SHA-256 인증(평문 비밀번호가 망에 흐르지 않음).
"""

import asyncio
import struct
from dataclasses import dataclass

from scramp import ScramClient

from app import protocol as pg

CONNECT_TIMEOUT_SEC = 10


class UpstreamError(Exception):
    pass


@dataclass
class Upstream:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    # 로그인 성공 뒤 서버가 보낸 ParameterStatus·BackendKeyData·ReadyForQuery 등
    # — DB 툴에 그대로 전달
    greeting: list[tuple[bytes, bytes]]
    backend_key: bytes | None  # 취소 요청(CancelRequest)을 이 연결로 이어 주기 위한 (pid, secret)


async def connect(
    host: str, port: int, user: str, password: str, database: str, params: dict[str, str]
) -> Upstream:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), CONNECT_TIMEOUT_SEC
        )
    except (OSError, TimeoutError) as error:
        raise UpstreamError("platform database is unreachable") from error
    try:
        writer.write(pg.startup_packet({**params, "user": user, "database": database}))
        await writer.drain()
        await asyncio.wait_for(_authenticate(reader, writer, user, password), CONNECT_TIMEOUT_SEC)
        greeting, backend_key = await asyncio.wait_for(_greeting(reader), CONNECT_TIMEOUT_SEC)
    except BaseException:
        writer.close()
        raise
    return Upstream(reader, writer, greeting, backend_key)


async def _authenticate(reader, writer, user: str, password: str) -> None:
    scram: ScramClient | None = None
    while True:
        tag, body = await pg.read_message(reader)
        if tag == b"E":
            raise UpstreamError(pg.error_fields(body).get("M", "authentication failed"))
        if tag != b"R":
            raise UpstreamError(f"unexpected message during authentication: {tag!r}")
        (code,) = struct.unpack("!I", body[:4])
        data = body[4:]
        if code == pg.AUTH_OK:
            return
        if code == pg.AUTH_SASL:
            mechanisms = [m.decode() for m in data.split(b"\0") if m]
            if "SCRAM-SHA-256" not in mechanisms:
                raise UpstreamError(f"unsupported SASL mechanisms: {mechanisms}")
            scram = ScramClient(["SCRAM-SHA-256"], user, password)
            first = scram.get_client_first().encode()
            writer.write(
                pg.message(b"p", b"SCRAM-SHA-256\0" + struct.pack("!i", len(first)) + first)
            )
        elif code == pg.AUTH_SASL_CONTINUE and scram is not None:
            scram.set_server_first(data.decode())
            writer.write(pg.message(b"p", scram.get_client_final().encode()))
        elif code == pg.AUTH_SASL_FINAL and scram is not None:
            scram.set_server_final(data.decode())  # 서버도 비밀번호를 아는지 확인 (상호 인증)
            continue
        elif code == pg.AUTH_CLEARTEXT:
            # 내부망 전용 설정 — 운영 platform-db는 SCRAM이다
            writer.write(pg.message(b"p", password.encode() + b"\0"))
        else:
            raise UpstreamError(f"unsupported authentication method: {code}")
        await writer.drain()


async def _greeting(reader) -> tuple[list[tuple[bytes, bytes]], bytes | None]:
    messages, backend_key = [], None
    while True:
        tag, body = await pg.read_message(reader)
        if tag == b"E":
            raise UpstreamError(pg.error_fields(body).get("M", "startup failed"))
        messages.append((tag, body))
        if tag == b"K":
            backend_key = body
        if tag == b"Z":
            return messages, backend_key
