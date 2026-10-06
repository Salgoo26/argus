"""PostgreSQL 통신 규약(프로토콜 v3)의 메시지 틀 — 게이트웨이가 읽고 쓰는 최소한만

메시지 = 종류 1바이트 + 길이 4바이트(자기 자신 포함) + 본문.
연결 첫 메시지(StartupMessage·SSLRequest·CancelRequest)만 종류 바이트 없이
길이 + 코드로 시작한다.
https://www.postgresql.org/docs/16/protocol-message-formats.html
"""

import asyncio
import struct

PROTOCOL_3 = 3 << 16  # 196608
SSL_REQUEST = 80877103
GSSENC_REQUEST = 80877104
CANCEL_REQUEST = 80877102

MAX_STARTUP_LENGTH = 10_000  # 시작 메시지는 매개변수 몇 개뿐 — 큰 값은 비정상
MAX_PASSWORD_LENGTH = 4_096  # 토큰(JWT)은 수백 바이트


class ProtocolError(Exception):
    pass


async def read_startup(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    (length,) = struct.unpack("!I", await reader.readexactly(4))
    if not 8 <= length <= MAX_STARTUP_LENGTH:
        raise ProtocolError("invalid startup packet length")
    payload = await reader.readexactly(length - 4)
    (code,) = struct.unpack("!I", payload[:4])
    return code, payload[4:]


async def read_message(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    tag = await reader.readexactly(1)
    (length,) = struct.unpack("!I", await reader.readexactly(4))
    if length < 4:
        raise ProtocolError("invalid message length")
    return tag, await reader.readexactly(length - 4)


def message(tag: bytes, body: bytes = b"") -> bytes:
    return tag + struct.pack("!I", len(body) + 4) + body


def startup_packet(params: dict[str, str]) -> bytes:
    body = struct.pack("!I", PROTOCOL_3)
    body += b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in params.items()) + b"\0"
    return struct.pack("!I", len(body) + 4) + body


def parse_params(payload: bytes) -> dict[str, str]:
    parts = payload.split(b"\0")
    params = {}
    for i in range(0, len(parts) - 1, 2):
        if not parts[i]:
            break
        params[parts[i].decode("utf-8", "replace")] = parts[i + 1].decode("utf-8", "replace")
    return params


def error_response(code: str, text: str, severity: str = "FATAL") -> bytes:
    """ErrorResponse — DB 툴 화면에 그대로 보이는 메시지. 입력값(토큰 등)을 되풀이하지 않는다"""
    fields = (
        b"S" + severity.encode() + b"\0"
        + b"V" + severity.encode() + b"\0"
        + b"C" + code.encode() + b"\0"
        + b"M" + text.encode() + b"\0"
    )  # fmt: skip
    return message(b"E", fields + b"\0")


def auth_request(code: int, data: bytes = b"") -> bytes:
    return message(b"R", struct.pack("!I", code) + data)


AUTH_OK = 0
AUTH_CLEARTEXT = 3
AUTH_MD5 = 5
AUTH_SASL = 10
AUTH_SASL_CONTINUE = 11
AUTH_SASL_FINAL = 12


def negotiate_protocol_version(minor: int = 0) -> bytes:
    """클라이언트가 3.0보다 높은 부 버전을 청하면 3.0으로 맞춰 달라고 답한다"""
    return message(b"v", struct.pack("!II", minor, 0))


def error_fields(body: bytes) -> dict[str, str]:
    fields, pos = {}, 0
    while pos < len(body) and body[pos] != 0:
        end = body.index(b"\0", pos + 1)
        fields[chr(body[pos])] = body[pos + 1 : end].decode("utf-8", "replace")
        pos = end + 1
    return fields
