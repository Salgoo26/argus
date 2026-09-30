"""시드 — 가상 데이터 형식, 재현성, 멱등, 취급자 동기화 outbox 적재"""

from sqlalchemy import func, select

from app.auth.passwords import verify_password
from app.models import member, operator, outbox
from app.scripts.seed import FIRST_MEMBER_ID, SEED_OPERATORS, seed

from conftest import TEST_PASSWORD

MEMBERS = 30  # 테스트는 작게 — 로직은 500명과 같다


def _seed(engine) -> bool:
    with engine.begin() as conn:
        return seed(conn, TEST_PASSWORD, member_count=MEMBERS)


def test_seed_creates_fictional_members(engine):
    assert _seed(engine) is True
    with engine.connect() as conn:
        rows = conn.execute(select(member).order_by(member.c.id)).mappings().all()

    assert len(rows) == MEMBERS
    assert rows[0]["id"] == FIRST_MEMBER_ID  # 5자리 id부터
    # 실존 주소·번호와 겹치지 않는 형식 (CLAUDE.md 3절 #2)
    assert all(r["email"].endswith("@example.com") for r in rows)
    assert all(r["phone"].startswith("010-0000-") for r in rows)
    assert all(r["name"] and r["address"] for r in rows)
    # 고객 로그인은 범위 밖 — 알려진 비밀번호로는 로그인되지 않는다
    assert not verify_password(rows[0]["password_hash"], TEST_PASSWORD)


def test_seed_is_reproducible(engine):
    _seed(engine)
    with engine.connect() as conn:
        first = conn.execute(select(member.c.id, member.c.name, member.c.address)).all()
    with engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE operator, member, outbox RESTART IDENTITY")
    _seed(engine)
    with engine.connect() as conn:
        second = conn.execute(select(member.c.id, member.c.name, member.c.address)).all()
    assert first == second  # 누가 몇 번 실행해도 같은 회원이 같은 id로


def test_seed_is_idempotent(engine):
    assert _seed(engine) is True
    assert _seed(engine) is False
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(member)).scalar_one() == MEMBERS
        assert conn.execute(select(func.count()).select_from(operator)).scalar_one() == len(
            SEED_OPERATORS
        )


def test_seed_operators_can_log_in(engine, client):
    _seed(engine)
    for login_id, *_ in SEED_OPERATORS:
        res = client.post(
            "/admin/auth/login", json={"login_id": login_id, "password": TEST_PASSWORD}
        )
        assert res.status_code == 200, login_id


def test_seed_enqueues_handler_created_per_operator(engine):
    _seed(engine)
    with engine.connect() as conn:
        rows = conn.execute(select(outbox).order_by(outbox.c.id)).mappings().all()
        ops = {
            r["login_id"]: r
            for r in conn.execute(select(operator)).mappings()  # 생성 시각 대조용
        }

    assert [r["topic"] for r in rows] == ["HANDLER"] * len(SEED_OPERATORS)
    assert all(r["status"] == "PENDING" for r in rows)
    payload = rows[0]["payload"]
    assert payload["type"] == "HANDLER_CREATED"
    assert str(rows[0]["event_id"]) == payload["event_id"]
    # api-spec 3-1 형식, 최소수집 — 비밀번호 해시·권한은 보내지 않는다
    assert payload["handler"] == {
        "login_id": "ops_park",
        "name": "박지훈",
        "team": "OPS",
        "employment_status": "ACTIVE",
    }
    # 마이크로초 정밀도 (api-spec 3-1 #4)
    assert payload["occurred_at"] == ops["ops_park"]["created_at"].isoformat(
        timespec="microseconds"
    )
