"""정보주체 식별값 마스킹 (LOG-10, actor-flows 8절, db-schema 2-1)

저장 "10293" → 표시 member_10293 → 마스킹 member_10***

회원 내부 PK도 회원 테이블과 쉽게 결합해 개인을 알아볼 수 있어 개인정보다(PIPA §2 1호 나목).
**서버에서 가려서 보낸다** — 원본 식별값이 브라우저에 도착하지 않게.
담당자의 사유 입력 후 해제(UNMASK)는 v0.1 여유 항목 ②라 아직 없다.
그때까지 담당자·취급자 모두 마스킹된 값만 받는다.
"""

PREFIXES = {"MEMBER": "member_"}  # 접두어는 표시 계층에서만 붙인다 (api-spec 1-7)
VISIBLE_UNTIL = -3  # 뒤 3자리를 가린다


def mask_subject(subject_type: str | None, subject_id: str) -> str:
    prefix = PREFIXES.get(subject_type or "", "")
    # 3자리 이하면 전부 가린다 — "뒤 3자리 마스킹"이 곧 전체 노출이 되지 않게
    visible = subject_id[:VISIBLE_UNTIL] if len(subject_id) > 3 else ""
    return f"{prefix}{visible}***"
