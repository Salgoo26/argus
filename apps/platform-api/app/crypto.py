"""결제수단 앱 레벨 암호화 — AES-256-GCM (안전성 확보조치 기준 §7②5·6호, CLAUDE.md 3절 #11)

- 키는 .env(PLATFORM_PAYMENT_ENCRYPTION_KEY)에만 있고 DB에는 없다 — DB가 통째로 유출돼도
  계좌번호는 읽을 수 없다. DB 기능(pgcrypto)이 아니라 앱에서 암호화하는 이유도 같다: 키가 SQL로
  DB 서버에 넘어가지 않는다
- GCM은 암호화와 함께 **변조 탐지 태그**를 붙인다 — 저장된 값이 바뀌면 복호화가 실패한다
- 연관 데이터(AAD)로 "어느 테이블의 몇 번 회원 값인지"를 묶는다 — 다른 회원 행에 암호문을
  옮겨 붙이면 복호화가 실패한다(암호문 바꿔치기 방지)
- 저장 형식: 버전 1바이트 + nonce 12바이트 + 암호문·태그. 버전은 키 교체 때 구분용

Spring이라면 JPA AttributeConverter에서 같은 일을 한다.
"""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = b"\x01"
NONCE_SIZE = 12  # GCM 권장 길이. 매번 무작위 — 같은 키로 nonce가 겹치면 GCM 안전성이 깨진다


class FieldCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 key must be 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, context: str) -> bytes:
        nonce = os.urandom(NONCE_SIZE)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return FORMAT_VERSION + nonce + sealed

    def decrypt(self, blob: bytes, context: str) -> str:
        """키·AAD가 다르거나 값이 변조됐으면 cryptography.exceptions.InvalidTag"""
        if blob[:1] != FORMAT_VERSION:
            raise ValueError("unknown ciphertext format")
        nonce, sealed = blob[1 : 1 + NONCE_SIZE], blob[1 + NONCE_SIZE :]
        return self._aead.decrypt(nonce, sealed, context.encode()).decode()


def refund_account_context(member_id: int) -> str:
    return f"refund_account:{member_id}"
