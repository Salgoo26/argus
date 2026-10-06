"""DB 툴 ↔ 게이트웨이 구간 TLS (architecture 3-4 — v0.1부터 적용, §7④ 전송구간 암호화)

게이트웨이가 토큰을 읽으려면 DB 툴이 비밀번호를 평문(cleartext) 방식으로 보내야 하므로 TLS가 필수다.
v0.1은 자체 서명 인증서 — DB 툴은 sslmode=require(암호화만, 서버 검증 없음). 중간자 공격에 열려 있어
운영 배포 시 사설 CA + verify-full 또는 공인 인증서로 바꾼다(architecture 8-6).
"""

import datetime
import os
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CERT_DAYS = 365


def ensure_self_signed(directory: Path, hostname: str = "db-gateway") -> tuple[Path, Path]:
    """없으면 만든다. 키 파일은 게이트웨이 계정만 읽을 수 있게(0600)"""
    cert_file, key_file = directory / "server.crt", directory / "server.key"
    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname), x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_file, key_file


def server_context(cert_file: Path, key_file: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file, key_file)
    return context
