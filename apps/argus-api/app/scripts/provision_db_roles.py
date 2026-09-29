"""API가 쓸 DB 로그인 계정을 만들고 argus_app 롤에 가입시킨다 (없으면 생성, 있으면 비밀번호 갱신).

마이그레이션(0002)은 권한만 가진 NOLOGIN 롤을 만들고, 비밀번호가 있는 로그인 계정은 여기서 만든다.
비밀번호가 마이그레이션 코드·이력에 남지 않게 하기 위함이다. 소유자 계정으로 실행한다.

    python -m app.scripts.provision_db_roles
    (환경변수: ARGUS_DB_* = 소유자 접속 정보, ARGUS_APP_DB_USER / ARGUS_APP_DB_PASSWORD)
"""

import os
import re

import psycopg
from psycopg import sql

from app.config import Settings

APP_ROLE = "argus_app"
_LOGIN_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def provision_app_login(conn: psycopg.Connection, login: str, password: str) -> None:
    if not _LOGIN_NAME.fullmatch(login) or login in (APP_ROLE, "argus_purge"):
        raise ValueError(f"invalid app login name: {login!r}")
    if len(password) < 16:
        raise ValueError("app DB password must be at least 16 characters")

    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone()
    # 롤 이름·비밀번호는 바인드 파라미터를 쓸 수 없는 DDL이라 sql.Identifier/Literal로 인용한다
    if exists:
        stmt = "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
    else:
        stmt = "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
    conn.execute(sql.SQL(stmt).format(sql.Identifier(login), sql.Literal(password)))
    conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(APP_ROLE), sql.Identifier(login)))


def main() -> None:
    settings = Settings()
    login = os.environ["ARGUS_APP_DB_USER"]
    password = os.environ["ARGUS_APP_DB_PASSWORD"]
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        autocommit=True,
    ) as conn:
        provision_app_login(conn, login, password)
    print(f"provisioned DB login '{login}' (member of {APP_ROLE})")


if __name__ == "__main__":
    main()
