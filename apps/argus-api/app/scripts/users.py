"""Argus 계정 관리 — python -m app.scripts.users <명령> <login_id>

    create-officer <login_id> --reason 사유   정보보호 담당자(A4) 계정 생성
    set-password   <login_id>   비밀번호 설정
                                (A5는 동기화로 계정만 생기고 로그인 불가 상태 → 여기서 설정)
    unlock         <login_id> --reason 사유   로그인 5회 실패로 잠긴(LOCKED) 계정 해제

권한을 바꾸는 명령(create-officer·unlock)은 사유가 필수이고 계정 이력(argus_user_history)에
처리자 "운영 스크립트"(NULL)로 남는다 — 화면(계정 관리)과 같은 이력 (v0.1 보강 L-4).
set-password는 권한 변경이 아니라 이력에 남기지 않는다.

비밀번호는 명령줄에 쓰지 않고 실행 후 입력창에서 받는다
— 명령줄 인자는 셸 기록(history)·프로세스 목록에 남는다.
자동화(M6 E2E 등)에서는 --password-env 변수명 으로 환경변수에서 읽는다.

실행 예 (Git Bash):
    docker compose exec argus-api python -m app.scripts.users create-officer officer \
        --reason "최초 담당자"
담당자 전환·재활성화 등은 Argus 계정 관리 화면에서(v0.1 보강 L-4).
비밀번호는 화면에서 다루지 않아 스크립트로 둔다.
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
from app.users.history import record_user_change

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


def _account(conn, login_id: str):
    return conn.execute(
        select(
            argus_user.c.id,
            argus_user.c.login_id,
            argus_user.c.role,
            argus_user.c.status,
            argus_user.c.last_login_at,
        ).where(argus_user.c.login_id == login_id)
    ).first()


def create_officer(conn, login_id: str, password: str, reason: str) -> str:
    """담당자 계정 생성. 돌려주는 값: "created" 또는 "promoted"

    플랫폼 백오피스 아이디 = Argus 아이디 원칙(2026-10-02 사용자 결정): 담당자도 플랫폼 계정을
    같은 아이디로 가진다. 플랫폼 시드 → 취급자 동기화가 먼저 돌면 그 아이디로 **로그인할 수 없는
    A5(취급자) 계정**이 이미 생겨 있다 → 한 번도 로그인한 적 없는 그 계정만 담당자로 전환한다.
    명부 연결(handler_id)은 남긴다 — 플랫폼에서 퇴직 처리되면 Argus 담당자 계정도 함께 막힌다.
    실제로 쓰던 취급자 계정(로그인 이력 있음)은 바꾸지 않는다 — 권한 변경은 사람이 따로 판단.
    계정 이력에 GRANT(처리자 = 운영 스크립트)로 남긴다 (v0.1 보강 L-4).
    """
    if not _LOGIN_ID.fullmatch(login_id):
        raise UsageError("login_id는 공백 없는 출력 가능 ASCII 1~64자")
    reason = _require_reason(reason)
    existing = _account(conn, login_id)
    if existing is not None:
        if existing.role != "HANDLER" or existing.last_login_at is not None:
            raise UsageError(f"이미 있는 계정: {login_id}")
        conn.execute(
            update(argus_user)
            .where(argus_user.c.login_id == login_id)
            .values(role="OFFICER", password_hash=hash_password(password), failed_login_count=0)
        )
        record_user_change(
            conn,
            user=existing,
            before=existing,
            change_type="GRANT",
            after_role="OFFICER",
            after_status=existing.status,
            reason=reason,
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
    record_user_change(
        conn,
        user=_account(conn, login_id),
        change_type="GRANT",
        after_role="OFFICER",
        after_status="ACTIVE",
        reason=reason,
    )
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


def unlock(conn, login_id: str, reason: str) -> None:
    reason = _require_reason(reason)
    account = _account(conn, login_id)
    if account is None:
        raise UsageError(f"없는 계정: {login_id}")
    if account.status != "LOCKED":
        # DISABLED(퇴직 등)는 해제 대상이 아니다 — 권한 복구는 사람이 별도로 판단
        raise UsageError(f"잠긴 계정이 아닙니다 (status={account.status})")
    conn.execute(
        update(argus_user)
        .where(argus_user.c.login_id == login_id)
        .values(status="ACTIVE", failed_login_count=0)
    )
    record_user_change(
        conn,
        user=account,
        before=account,
        change_type="UNLOCK",
        after_role=account.role,
        after_status="ACTIVE",
        reason=reason,
    )


def _require_reason(reason: str | None) -> str:
    """권한을 바꾸는 명령은 사유 필수 (안내서 62 — 사유 없는 권한 내역은 미흡 사례)"""
    reason = (reason or "").strip()
    if not reason or len(reason) > 500:
        raise UsageError("사유(--reason)를 1~500자로 적어 주세요")
    return reason


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.scripts.users")
    parser.add_argument("command", choices=["create-officer", "set-password", "unlock"])
    parser.add_argument("login_id")
    parser.add_argument("--password-env", help="비밀번호를 읽을 환경변수 이름 (자동화용)")
    parser.add_argument("--reason", help="권한 변경 사유 — create-officer·unlock은 필수")
    args = parser.parse_args(argv)

    engine = create_engine(Settings().database_url())
    try:
        if args.command != "set-password":
            _require_reason(args.reason)  # 비밀번호를 묻기 전에 확인
        password = None if args.command == "unlock" else read_password(args.password_env)
        with engine.begin() as conn:
            if args.command == "create-officer":
                if create_officer(conn, args.login_id, password, args.reason) == "promoted":
                    print(
                        "동기화로 생긴(로그인 이력 없는) 취급자 계정을 담당자 계정으로 전환합니다"
                    )
            elif args.command == "set-password":
                set_password(conn, args.login_id, password)
            else:
                unlock(conn, args.login_id, args.reason)
    except UsageError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"완료: {args.command} {args.login_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
