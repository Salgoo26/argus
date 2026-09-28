"""M0 스모크 테스트 — CI 파이프라인이 동작하는지 확인한다.

- 테스트 실행 자체
- Postgres 서비스 컨테이너(DATABASE_URL) 연결

실제 기능 테스트는 M2부터 추가한다.
"""

import os

import pytest

import app


def test_package_importable():
    assert app.__name__ == "app"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL 미설정 — CI 또는 compose DB를 대상으로 실행할 때만 검사",
)
def test_database_reachable():
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        server_version = conn.info.server_version

    assert server_version // 10000 == 16  # PostgreSQL 16 (architecture 1절)
