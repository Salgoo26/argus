"""카탈로그 캐시 — 사용자 함수 목록 (architecture 3-4 "기록 제외" 예외)"""

import asyncio

from psycopg.conninfo import make_conninfo

from app.catalog import Catalog


def test_first_lookup_queries_even_right_after_boot(upstream_db, monkeypatch):
    # 부팅 직후엔 time.monotonic()이 60초보다 작다 — 그래도 첫 조회는 DB를 봐야 한다 (CI에서 발견)
    monkeypatch.setattr("app.catalog.time.monotonic", lambda: 5.0)
    catalog = Catalog(make_conninfo(**upstream_db))
    names = asyncio.run(catalog.user_functions())
    assert {"member_email", "touch_member"} <= names
