"""Alembic 실행 환경.

스키마 원본은 docs/db-schema.md의 DDL이고, 리비전은 그 DDL을 그대로 옮긴다
(autogenerate 미사용 — 부분 유니크 인덱스·CHECK·트리거·권한을 정확히 재현하기 위해).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url():
    # 테스트는 config.attributes["url"]로 임시 DB를 넘긴다
    url = config.attributes.get("url")
    if url is not None:
        return url
    from app.config import Settings

    return Settings().database_url()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline 모드는 지원하지 않는다 — DB에 직접 적용한다")
run_migrations_online()
