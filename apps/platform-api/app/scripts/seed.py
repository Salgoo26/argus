"""시드 데이터 — python -m app.scripts.seed (compose의 platform-seed가 기동 시 실행)

모든 데이터는 가상이다 (CLAUDE.md 3절 #2, README):
- 이름·주소: Faker ko_KR의 무작위 조합
- 이메일: example.com — 인터넷 표준(RFC 2606)이 예시용으로 예약한 도메인이라 실존 주소가 될 수 없다
- 전화번호: 010-0000-XXXX — 가운데를 0000으로 고정해 실존 번호와 겹치지 않게
- 회원 비밀번호: 고객 로그인은 Skeleton 범위 밖 → 아무도 모르는 무작위 해시 하나를 공유

재현성: Faker·난수 시드를 고정해 누가 몇 번 실행해도 같은 회원이 같은 id로 생긴다
(M6 자동 시나리오 테스트의 전제). 테이블이 비어 있을 때만 넣으므로 재실행해도 늘어나지 않는다.

기준선용 과거 접속기록(baseline.py)은 outbox에 넣어 relay → Argus 수집 API로 보낸다.

플랫폼 DB에는 직접 INSERT한다. "시드도 수집 API로"(CLAUDE.md 3절 #5)는 Argus 원장
(access_log)의 해시체인 때문에 생긴 규칙이다. 취급자는 outbox를 거쳐 Argus에 동기화된다.
"""

import logging
import random
import sys
from datetime import timedelta

from faker import Faker
from sqlalchemy import Connection, create_engine, func, insert, select, text

from app.auth.passwords import hash_password, unusable_password_hash
from app.config import Settings
from app.models import member, operator
from app.outbox import enqueue, handler_event
from app.scripts.baseline import baseline_events

logger = logging.getLogger("seed")

SEED = 20260929
MEMBER_COUNT = 500
FIRST_MEMBER_ID = 10001  # 5자리 id — 마스킹 표시(member_10***)가 의미 있게 보이도록
MIN_PASSWORD_LENGTH = 12

# (login_id, 이름, 소속, 권한) — 가상 인물. ops_park이 시나리오의 대량 다운로드 주인공
SEED_OPERATORS = (
    ("ops_park", "박지훈", "OPS", "OPS"),
    ("mkt_lee", "이수민", "MARKETING", "MARKETING"),
    ("cs_kim", "김민지", "CS", "CS"),
    ("cs_choi", "최유나", "CS", "CS"),
    ("admin_han", "한도윤", "OPS", "ADMIN"),
)


def is_seeded(conn: Connection) -> bool:
    counts = [
        conn.execute(select(func.count()).select_from(table)).scalar_one()
        for table in (operator, member)
    ]
    return any(counts)


def seed(conn: Connection, operator_password: str, member_count: int = MEMBER_COUNT) -> bool:
    """비어 있으면 넣고 True, 이미 데이터가 있으면 아무것도 하지 않고 False"""
    if is_seeded(conn):
        return False

    fake = Faker("ko_KR")
    fake.seed_instance(SEED)
    rng = random.Random(SEED)  # noqa: S311 — 가짜 가입일 분산용, 보안 용도 아님(재현성 위해 고정 시드)
    now = conn.execute(select(func.now())).scalar_one()

    conn.execute(
        text("SELECT setval(pg_get_serial_sequence('member', 'id'), :last, true)"),
        {"last": FIRST_MEMBER_ID - 1},
    )
    shared_hash = unusable_password_hash()
    conn.execute(
        insert(member),
        [
            {
                "email": f"user{n:04d}@example.com",
                "password_hash": shared_hash,
                "name": fake.name(),
                "phone": f"010-0000-{n:04d}",
                "address": fake.address(),
                "created_at": now - timedelta(seconds=rng.randint(0, 365 * 24 * 3600)),
            }
            for n in range(1, member_count + 1)
        ],
    )

    for login_id, name, team, role in SEED_OPERATORS:
        row = conn.execute(
            insert(operator)
            .values(
                login_id=login_id,
                password_hash=hash_password(operator_password),
                name=name,
                team=team,
                role=role,
            )
            .returning(operator)
        ).one()
        # 취급자 생성과 동기화 통지를 같은 트랜잭션으로 — 둘 다 커밋되거나 둘 다 취소
        enqueue(conn, "HANDLER", handler_event("HANDLER_CREATED", row, row.created_at))

    # 기준선용 과거 접속기록 (기능 레이어 4) — Argus 원장에는 relay → 수집 API로만 들어간다.
    # 취급자 동기화(HANDLER)가 먼저 나가도록 relay가 topic 순서를 지킨다
    actors = [login_id for login_id, *_ in SEED_OPERATORS]
    for event in baseline_events(actors, FIRST_MEMBER_ID, member_count, now, rng):
        enqueue(conn, "ACCESS_LOG", event)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [seed] %(message)s")
    settings = Settings()
    secret = settings.seed_operator_password
    password = secret.get_secret_value() if secret else ""
    if len(password) < MIN_PASSWORD_LENGTH:
        logger.error(
            "PLATFORM_SEED_OPERATOR_PASSWORD must be at least %d chars", MIN_PASSWORD_LENGTH
        )
        return 1

    engine = create_engine(settings.database_url())
    try:
        with engine.begin() as conn:
            created = seed(conn, password)
    finally:
        engine.dispose()

    if created:
        logger.info("seeded %d members, %d operators", MEMBER_COUNT, len(SEED_OPERATORS))
    else:
        logger.info("already seeded — skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
