"""주문·결제 공통 값 (기능 레이어 7 ①)

카드사·은행 이름은 모두 가상이다 — 실존 회사로 오인되지 않게.
PG(결제대행사)도 가상이다: 실제 결제는 일어나지 않고, 결제창은 흉내만 낸다(목업).
"""

import secrets

CARD_COMPANIES = ("하늘카드", "바다카드", "숲카드", "들판카드")
BANKS = ("하늘은행", "바다은행", "숲은행", "들판은행")
MOCK_PG_NAME = "데모페이(가상 PG)"


def mock_pg_tid() -> str:
    """가상 PG 거래번호 — 실제라면 PG사가 승인 응답으로 돌려주는 값"""
    return "MOCKPG-" + secrets.token_hex(10).upper()
