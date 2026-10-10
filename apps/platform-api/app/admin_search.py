"""관리자 검색 공통 — 회원·주문·1:1 문의 (v0.1 보강 E, 안내서 129쪽 누락 사례 ②)

- 검색 조건은 **POST 본문**으로 받는다 — 이름·이메일·연락처가 URL에 실리면 서버 접근 로그·
  브라우저 방문 기록에 남는다 (Argus 접속기록 검색과 같은 방식)
- 접속기록에는 조건의 **키 이름만**(record_query_keys), 정보주체는 결과로 화면에 보인 회원 PK 전부
- 결과 0건도 READ로 남는다(정보주체 0명) — 무엇을 찾으려 했는지는 키 이름으로 남는다
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.agent import record_query_keys

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 오프셋으로 충분

# 부분 일치 검색어 — 앞뒤 공백 정리, 빈 문자열은 거부
SearchText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SearchPage(BaseModel):
    page: Annotated[int, Field(ge=1)] = 1
    size: Annotated[int, Field(ge=1, le=100)] = 20

    def record_keys(self) -> None:
        """실제로 넣은 조건의 이름만 접속기록에 (값은 남기지 않는다)"""
        record_query_keys(self.model_dump(exclude_unset=True, exclude_none=True))


def check_period(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise ValueError("start date must not be after end date")


def kst_period(column, start: date | None, end: date | None) -> list:
    """한국 날짜 기준 [시작일 0시, 종료일 다음날 0시) — 양 끝 포함"""
    conditions = []
    if start is not None:
        conditions.append(column >= datetime.combine(start, time(), KST))
    if end is not None:
        conditions.append(column < datetime.combine(end + timedelta(days=1), time(), KST))
    return conditions
