"""DB 직접 접근(2티어) 기록의 화면 표시용 상세 (policy 1-5 "접속기록"·"취급자 화면")

담당자·취급자가 "무엇을 소명해야 하는지" 알 수 있게 정규화 SQL·테이블·컬럼·건수를 보여 준다.
원문 SQL·매개변수는 Argus에 없다(게이트웨이 원문 저장소에만 — 절대 규칙 #3). 정규화 SQL은 리터럴이
$n으로 바뀐 것이라 값(이메일 등)이 없고, 수집 단계에서 따옴표·달러 인용이 남은 것은 이미 거부했다.
"""

from typing import Any

# 화면에 보여 줄 context 키 — 그 밖의 키(DB 계정·토큰 ID 등)는 싣지 않는다
_SHOWN = ("sql_normalized", "tables", "columns", "row_count", "raw_ref")


def db_detail(access_path: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    """화면 경유 기록이면 None"""
    if access_path != "DB":
        return None
    context = context or {}
    detail = {key: context.get(key) for key in _SHOWN}
    # 처리한 정보주체를 특정하지 못한 기록 — 결과에 회원을 가리키는 열이 없던 조회 (v0.1 보강 G-1)
    detail["subject_unresolved"] = bool(context.get("subject_unresolved"))
    return detail
