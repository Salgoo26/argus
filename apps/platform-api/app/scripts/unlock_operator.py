"""잠긴 취급자 계정 해제 — python -m app.scripts.unlock_operator <login_id>

로그인 5회 연속 실패로 잠긴 계정의 실패 횟수를 0으로 되돌린다. 관리 UI는 Skeleton 범위 밖이라
스크립트로 둔다 (implementation-log 2026-09-29 설계 변경 3).
퇴직(TERMINATED) 계정은 해제 대상이 아니다 — 재직 상태 변경은 인사 처리로 한다.
"""

import sys

from sqlalchemy import create_engine, func, update

from app.config import Settings
from app.models import operator


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m app.scripts.unlock_operator <login_id>", file=sys.stderr)
        return 2
    login_id = argv[0]

    engine = create_engine(Settings().database_url())
    try:
        with engine.begin() as conn:
            updated = conn.execute(
                update(operator)
                .where(operator.c.login_id == login_id, operator.c.employment_status == "ACTIVE")
                .values(failed_login_count=0, updated_at=func.now())
            ).rowcount
    finally:
        engine.dispose()

    if updated == 0:
        print(f"no active operator: {login_id}", file=sys.stderr)
        return 1
    print(f"unlocked: {login_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
