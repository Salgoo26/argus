"""접속기록 원장 해시체인 검증 — python -m app.scripts.verify_chain (§8③ 위·변조 확인)

원장 전체를 id 순으로 다시 계산해, 각 레코드의 hash가 내용과 맞는지(수정 탐지)와
prev_hash가 앞 레코드의 hash로 이어지는지(삭제·끼워넣기 탐지)를 확인한다.
계산 규칙은 append와 같은 함수(app.ledger.hashchain)를 쓴다.

    종료 코드 0 = 체인 정상 / 1 = 체인 끊김(위치 출력) / 2 = 실행 오류(DB 접속 등)

실행 예 (Git Bash, 레포 루트):
    docker compose exec argus-api python -m app.scripts.verify_chain

API와 같은 앱 계정(원장 SELECT만 필요)으로 실행한다 — 검증에 소유자 권한은 필요 없다.
파기 배치(v0.1 범위 밖)가 생기면 destruction_history.chain_anchor_hash를 시작점으로 넘겨야 한다.
"""

import sys

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.ledger.hashchain import ChainVerification, verify_chain


def run(engine) -> ChainVerification:
    # 1년치 원장을 한 번에 메모리에 올리지 않도록 서버 측 커서로 나눠 읽는다.
    # 단일 SELECT라 실행 중 새로 append되는 레코드와 섞이지 않는 한 시점의 스냅숏을 본다
    with engine.connect() as conn:
        return verify_chain(conn.execution_options(yield_per=1000))


def main() -> int:
    engine = create_engine(Settings().database_url())
    try:
        result = run(engine)
    except SQLAlchemyError as error:
        # 첫 줄만 — 이어지는 줄에는 실행한 SQL·파라미터가 붙는다
        print(f"오류: 원장을 읽지 못했습니다 — {str(error).splitlines()[0]}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    if result.ok:
        print(f"OK: 해시체인 정상 — {result.checked}건 확인")
        return 0
    print(
        f"FAIL: 해시체인 끊김 — id={result.broken_at_id} ({result.reason}), "
        f"그 앞 {result.checked}건은 정상",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
