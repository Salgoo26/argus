"""시드 — 가상 데이터 형식, 재현성, 멱등, 취급자 동기화 outbox 적재"""

from sqlalchemy import func, select

from app.auth.passwords import verify_password
from app.models import member, member_consent, operator, outbox
from app.scripts.seed import FIRST_MEMBER_ID, SEED_OPERATORS, backfill_consents, seed

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
        conn.exec_driver_sql(
            "TRUNCATE operator, member, member_consent, outbox, orders, payment, refund_account,"
            " inquiry, db_access_token"
            " RESTART IDENTITY"
        )
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
        rows = (
            conn.execute(select(outbox).where(outbox.c.topic == "HANDLER").order_by(outbox.c.id))
            .mappings()
            .all()
        )
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


# ── 기준선용 과거 접속기록 (기능 레이어 4) ─────────────────


def test_seed_enqueues_baseline_access_logs_after_handlers(engine):
    _seed(engine)
    with engine.connect() as conn:
        topics = conn.execute(select(outbox.c.topic).order_by(outbox.c.id)).scalars().all()
    # 취급자 동기화가 먼저 — Argus 명부가 기록보다 앞서 있게
    assert topics[: len(SEED_OPERATORS)] == ["HANDLER"] * len(SEED_OPERATORS)
    assert set(topics[len(SEED_OPERATORS) :]) == {"ACCESS_LOG"}


def test_backfill_gives_seeded_members_consent_history_once(engine):
    _seed(engine)
    with engine.begin() as conn:
        assert backfill_consents(conn) == MEMBERS
    with engine.begin() as conn:
        assert backfill_consents(conn) == 0  # 이미 이력이 있으면 건드리지 않는다
        rows = conn.execute(
            select(member_consent.c.item_code, member_consent.c.agreed, member_consent.c.client_ip)
        ).all()

    assert len(rows) == MEMBERS * 4
    assert all(agreed for code, agreed, _ in rows if code != "MARKETING")  # 필수는 모두 동의
    marketing = [agreed for code, agreed, _ in rows if code == "MARKETING"]
    assert 0 < sum(marketing) < MEMBERS  # 선택은 일부만
    assert all(ip is None for *_, ip in rows)  # 알 수 없는 IP는 지어내지 않는다


def test_commerce_seed_has_no_card_numbers_and_encrypted_accounts(engine):
    from app.crypto import FieldCipher, refund_account_context
    from app.models import orders, payment, refund_account
    from app.scripts.seed import seed_commerce

    from conftest import TEST_PAYMENT_KEY

    cipher = FieldCipher(bytes.fromhex(TEST_PAYMENT_KEY))
    _seed(engine)
    with engine.begin() as conn:
        assert seed_commerce(conn, cipher) is True
    with engine.begin() as conn:
        assert seed_commerce(conn, cipher) is False  # 주문이 있으면 다시 넣지 않는다
        order_count = conn.execute(select(func.count()).select_from(orders)).scalar_one()
        paid = conn.execute(select(func.count()).select_from(payment)).scalar_one()
        accounts = conn.execute(select(refund_account)).mappings().all()

    assert order_count == paid > 0
    assert "card_number" not in payment.c  # PG 목업 — 카드번호 컬럼 자체가 없다
    for acc in accounts:
        number = cipher.decrypt(acc["account_number_enc"], refund_account_context(acc["member_id"]))
        assert number.startswith("0000") and number.endswith(acc["account_last4"])
        assert number.encode() not in acc["account_number_enc"]


def test_commerce_seed_skips_members_who_signed_up_on_screen(engine, client):
    from app.crypto import FieldCipher
    from app.models import orders, refund_account
    from app.scripts.seed import seed_commerce

    from conftest import TEST_PAYMENT_KEY, signup

    _seed(engine)
    signup(client, email="real-signup@example.com")
    with engine.begin() as conn:
        seed_commerce(conn, FieldCipher(bytes.fromhex(TEST_PAYMENT_KEY)))
        signed_up = conn.execute(
            select(member.c.id).where(member.c.email == "real-signup@example.com")
        ).scalar_one()
        owners = set(conn.execute(select(orders.c.member_id)).scalars())
        owners |= set(conn.execute(select(refund_account.c.member_id)).scalars())
    assert signed_up not in owners


def test_inquiry_seed_mixes_open_and_answered(engine):
    from app.models import inquiry
    from app.scripts.seed import INQUIRY_COUNT, seed_inquiries

    _seed(engine)
    with engine.begin() as conn:
        assert seed_inquiries(conn) is True
        assert seed_inquiries(conn) is False
        rows = conn.execute(select(inquiry.c.status, inquiry.c.answered_by)).all()
    assert len(rows) == INQUIRY_COUNT
    statuses = {s for s, _ in rows}
    assert statuses == {"OPEN", "ANSWERED"}
    assert all((s == "ANSWERED") == (by is not None) for s, by in rows)


def test_officer_operator_has_same_id_as_argus_officer_and_is_synced(engine, client):
    from app.scripts.seed import OFFICER_OPERATOR, ensure_officer_operator

    from conftest import login, outbox_payloads

    _seed(engine)
    with engine.begin() as conn:
        assert ensure_officer_operator(conn, TEST_PASSWORD) is True
        assert ensure_officer_operator(conn, TEST_PASSWORD) is False  # 있으면 그대로

    assert OFFICER_OPERATOR[0] == "officer"  # README의 Argus 담당자 아이디와 같다
    assert login(client, "officer").status_code == 200
    synced = [p["handler"]["login_id"] for p in outbox_payloads(engine, "HANDLER")]
    assert synced.count("officer") == 1
