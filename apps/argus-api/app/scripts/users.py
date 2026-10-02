"""Argus 계정 관리 — python -m app.scripts.users <명령> <login_id>

    create-officer <login_id>   정보보호 담당자(A4) 계정 생성
    set-password   <login_id>   비밀번호 설정
                                (A5는 동기화로 계정만 생기고 로그인 불가 상태 → 여기서 설정)
    unlock         <login_id>   로그인 5회 실패로 잠긴(LOCKED) 계정 해제

비밀번호는 명령줄에 쓰지 않고 실행 후 입력창에서 받는다
— 명령줄 인자는 셸 기록(history)·프로세스 목록에 남는다.
자동화(M6 E2E 등)에서는 --password-env 변수명 으로 환경변수에서 읽는다.

실행 예 (Git Bash):
    docker compose exec argus-api python -m app.scripts.users create-officer officer
관리 UI는 v0.1 범위 밖이라 스크립트로 둔다 (api-spec 3-1 v0.4, policy 4-3).
"""

import argparse
import getpass
import os
import re
import sys

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from app.config import Settings
from app.models import argus_user

_LOGIN_ID = re.compile(r"^[\x21-\x7e]{1,64}$")  # 수집 API의 login_id 규칙과 같게


class UsageError(Exception):
    pass


def read_password(env_name: str | None) -> str:
    if env_name:
        password = os.environ.get(env_name, "")
    else:
        password = getpass.getpass("새 비밀번호: ")
        if getpass.getpass("비밀번호 확인: ") != password:
            raise UsageError("비밀번호가 일치하지 않습니다")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UsageError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다")
    return password


def create_officer(conn, login_id: str, password: str) -> str:
    """담당자 계정 생성. 돌려주는 값: "created" 또는 "promoted"

    플랫폼 백오피스 아이디 = Argus 아이디 원칙(2026-10-02 사용자 결정): 담당자도 플랫폼 계정을
    같은 아이디로 가진다. 플랫폼 시드 → 취급자 동기화가 먼저 돌면 그 아이디로 **로그인할 수 없는
    A5(취급자) 계정**이 이미 생겨 있다 → 한 번도 로그인한 적 없는 그 계정만 담당자로 전환한다.
    명부 연결(handler_id)은 남긴다 — 플랫폼에서 퇴직 처리되면 Argus 담당자 계정도 함께 막힌다.
    실제로 쓰던 취급자 계정(로그인 이력 있음)은 바꾸지 않는다 — 권한 변경은 사람이 따로 판단.
    """
    if not _LOGIN_ID.fullmatch(login_id):
        raise UsageError("login_id는 공백 없는 출력 가능 ASCII 1~64자")
    existing = (
        conn.execute(
            select(argus_user.c.role, argus_user.c.last_login_at).where(
                argus_user.c.login_id == login_id
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if existing["role"] != "HANDLER" or existing["last_login_at"] is not None:
            raise UsageError(f"이미 있는 계정: {login_id}")
        conn.execute(
            update(argus_user)
            .where(argus_user.c.login_id == login_id)
            .values(role="OFFICER", password_hash=hash_password(password), failed_login_count=0)
        )
        return "promoted"
    try:
        conn.execute(
            insert(argus_user).values(
                login_id=login_id, password_hash=hash_password(password), role="OFFICER"
            )
        )
    except IntegrityError:
        raise UsageError(f"이미 있는 계정: {login_id}") from None
    return "created"


def set_password(conn, login_id: str, password: str) -> None:
    # 상태(잠김·비활성)는 바꾸지 않는다 — 퇴직으로 막힌 A5가 비밀번호 설정으로 되살아나지 않게
    updated = conn.execute(
        update(argus_user)
        .where(argus_user.c.login_id == login_id)
        .values(password_hash=hash_password(password))
    ).rowcount
    if not updated:
        raise UsageError(f"없는 계정: {login_id}")


def unlock(conn, login_id: str) -> None:
    status = conn.execute(
        select(argus_user.c.status).where(argus_user.c.login_id == login_id)
    ).scalar_one_or_none()
    if status is None:
        raise UsageError(f"없는 계정: {login_id}")
    if status != "LOCKED":
        # DISABLED(퇴직 등)는 해제 대상이 아니다 — 권한 복구는 사람이 별도로 판단
        raise UsageError(f"잠긴 계정이 아닙니다 (status={status})")
    conn.execute(
        update(argus_user)
        .where(argus_user.c.login_id == login_id)
        .values(status="ACTIVE", failed_login_count=0)
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.scripts.users")
    parser.add_argument("command", choices=["create-officer", "set-password", "unlock"])
    parser.add_argument("login_id")
    parser.add_argument("--password-env", help="비밀번호를 읽을 환경변수 이름 (자동화용)")
    args = parser.parse_args(argv)

    engine = create_engine(Settings().database_url())
    try:
        password = None if args.command == "unlock" else read_password(args.password_env)
        with engine.begin() as conn:
            if args.command == "create-officer":
                if create_officer(conn, args.login_id, password) == "promoted":
                    print(
                        "동기화로 생긴(로그인 이력 없는) 취급자 계정을 담당자 계정으로 전환합니다"
                    )
            elif args.command == "set-password":
                set_password(conn, args.login_id, password)
            else:
                unlock(conn, args.login_id)
    except UsageError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"완료: {args.command} {args.login_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
