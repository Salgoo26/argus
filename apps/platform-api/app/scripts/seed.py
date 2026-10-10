"""시드 데이터 — python -m app.scripts.seed (compose의 platform-seed가 기동 시 실행)

모든 데이터는 가상이다 (CLAUDE.md 3절 #2, README):
- 이름·주소(기본 배송지): Faker ko_KR의 무작위 조합, 우편번호는 9로 시작하는 가상 번호
- 이메일: example.com — 인터넷 표준(RFC 2606)이 예시용으로 예약한 도메인이라 실존 주소가 될 수 없다
- 전화번호: 010-0000-XXXX — 가운데를 0000으로 고정해 실존 번호와 겹치지 않게
- 회원 비밀번호: 아무도 모르는 무작위 해시 하나를 공유 — 시드 회원으로는 로그인할 수 없다
  (고객 화면은 회원가입으로 새 계정을 만들어 쓴다)

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
from typing import Any

from faker import Faker
from sqlalchemy import Connection, create_engine, exists, func, insert, select, text

from app.auth.passwords import hash_password, unusable_password_hash
from app.commerce import BANKS, CARD_COMPANIES
from app.config import Settings
from app.crypto import FieldCipher, refund_account_context
from app.models import (
    consent_item,
    inquiry,
    member,
    member_consent,
    operator,
    operator_permission_history,
    orders,
    payment,
    product,
    refund_account,
    shipping_address,
)
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


def record_initial_grant(conn: Connection, row: Any) -> None:
    """시드 계정의 권한 부여 이력 — 사유 "초기 계정", 처리자 시스템(NULL) (v0.1 보강 L-3)"""
    conn.execute(
        insert(operator_permission_history).values(
            operator_id=row.id,
            change_type="GRANT",
            after_role=row.role,
            after_team=row.team,
            after_status=row.employment_status,
            reason="초기 계정",
        )
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
    # Faker 호출 순서(이름 → 주소)를 회원마다 그대로 — 순서가 바뀌면 같은 시드로도 다른 회원이 된다
    people = []
    for n in range(1, member_count + 1):
        name, address = fake.name(), fake.address()
        created_at = now - timedelta(seconds=rng.randint(0, 365 * 24 * 3600))
        people.append((n, name, address, created_at))
    member_ids = conn.execute(
        insert(member).returning(member.c.id, sort_by_parameter_order=True),
        [
            {
                "email": f"user{n:04d}@example.com",
                "password_hash": shared_hash,
                "name": name,
                "phone": f"010-0000-{n:04d}",
                "created_at": created_at,
            }
            for n, name, _, created_at in people
        ],
    ).scalars()
    # 주소는 회원 정보가 아니라 배송지 (2026-10-07) — 회원마다 기본 배송지 1개.
    # 우편번호는 가상(회원 번호에서 만든 5자리), 받는 사람·연락처는 회원 본인
    conn.execute(
        insert(shipping_address),
        [
            {
                "member_id": member_id,
                "label": "집",
                "recipient": name,
                "phone": f"010-0000-{n:04d}",
                "zip_code": f"{90000 + n:05d}",
                "address": address,
                "is_default": True,
                "created_at": created_at,
                "updated_at": created_at,
            }
            for member_id, (n, name, address, created_at) in zip(member_ids, people, strict=True)
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
        record_initial_grant(conn, row)
        # 취급자 생성과 동기화 통지를 같은 트랜잭션으로 — 둘 다 커밋되거나 둘 다 취소
        enqueue(conn, "HANDLER", handler_event("HANDLER_CREATED", row, row.created_at))

    # 기준선용 과거 접속기록 (기능 레이어 4) — Argus 원장에는 relay → 수집 API로만 들어간다.
    # 취급자 동기화(HANDLER)가 먼저 나가도록 relay가 topic 순서를 지킨다
    actors = [login_id for login_id, *_ in SEED_OPERATORS]
    for event in baseline_events(actors, FIRST_MEMBER_ID, member_count, now, rng):
        enqueue(conn, "ACCESS_LOG", event)
    return True


MARKETING_AGREE_RATIO = 0.4  # 시드 회원 중 마케팅 수신 동의 비율 (가상)


def backfill_consents(conn: Connection) -> int:
    """동의 이력이 없는 회원에게 가입 시점의 동의 이력을 만든다 (기능 레이어 7 결정 2)

    seed()와 따로 매번 실행한다 — 고객 화면 이전에 시드된 개발 스택의 회원 500명에게도
    동의 이력이 생기게. 이미 이력이 있는 회원은 건드리지 않으므로 재실행해도 늘지 않는다.
    필수 동의는 모두, 마케팅은 일부만 동의. 접속 IP는 알 수 없어 비운다(지어내지 않는다).
    """
    items = conn.execute(select(consent_item.c.code, consent_item.c.version)).all()
    targets = conn.execute(
        select(member.c.id, member.c.created_at)
        .where(~exists().where(member_consent.c.member_id == member.c.id))
        .order_by(member.c.id)
    ).all()
    rng = random.Random(SEED + 1)  # noqa: S311 — 가짜 동의 분포용, 보안 용도 아님
    rows = []
    for member_id, created_at in targets:
        marketing = rng.random() < MARKETING_AGREE_RATIO
        for code, version in items:
            rows.append(
                {
                    "member_id": member_id,
                    "item_code": code,
                    "item_version": version,
                    "agreed": marketing if code == "MARKETING" else True,
                    "acted_at": created_at,
                    "client_ip": None,
                    "method": "WEB_FORM",
                }
            )
    if rows:
        conn.execute(insert(member_consent), rows)
    return len(targets)


ORDER_COUNT = 300
REFUND_ACCOUNT_COUNT = 60
ORDER_HISTORY_DAYS = 180


def seed_commerce(conn: Connection, cipher: FieldCipher) -> bool:
    """가상 주문·결제(PG 목업)·환불계좌 (기능 레이어 7 ①) — 주문이 하나도 없을 때만

    seed()와 따로 실행한다 — 주문 기능 이전에 시드된 개발 스택에도 들어가게.
    카드번호는 어디에도 없다(PG 목업). 환불계좌 번호는 0000으로 시작하는 가상 번호를 앱에서
    암호화해 넣는다 — 시드도 실제 저장 경로(암호화)와 같게.
    """
    if conn.execute(select(func.count()).select_from(orders)).scalar_one():
        return False
    # 시드 회원(user0001@example.com …)에게만 — 화면에서 가입한 계정에 가짜 주문이 붙지 않게
    members = (
        conn.execute(
            select(member.c.id)
            .where(member.c.email.like("user%@example.com"))
            .order_by(member.c.id)
        )
        .scalars()
        .all()
    )
    products = conn.execute(select(product.c.id, product.c.price)).all()
    if not members or not products:
        return False

    # 주문의 배송 정보 = 그 회원의 기본 배송지 복사 (7-4 ③ — 실제 주문 경로와 같게)
    shipping = {
        r.member_id: {
            "ship_recipient": r.recipient,
            "ship_phone": r.phone,
            "ship_zip_code": r.zip_code,
            "ship_address": r.address,
            "ship_address_detail": r.address_detail,
        }
        for r in conn.execute(select(shipping_address).where(shipping_address.c.is_default))
    }

    rng = random.Random(SEED + 2)  # noqa: S311 — 가짜 주문 분포용, 보안 용도 아님
    now = conn.execute(select(func.now())).scalar_one()
    for _ in range(ORDER_COUNT):
        product_id, price = rng.choice(products)
        ordered_at = now - timedelta(seconds=rng.randint(3600, ORDER_HISTORY_DAYS * 24 * 3600))
        member_id = rng.choice(members)
        order_id = conn.execute(
            insert(orders)
            .values(
                member_id=member_id,
                product_id=product_id,
                amount=price,
                status="PAID",
                ordered_at=ordered_at,
                **shipping.get(member_id, {}),
            )
            .returning(orders.c.id)
        ).scalar_one()
        conn.execute(
            insert(payment).values(
                order_id=order_id,
                method="CARD",
                card_company=rng.choice(CARD_COMPANIES),
                pg_tid=f"MOCKPG-SEED{order_id:010d}",
                amount=price,
                approved_at=ordered_at,
            )
        )

    for member_id in rng.sample(list(members), min(REFUND_ACCOUNT_COUNT, len(members))):
        number = f"0000{rng.randint(10**7, 10**8 - 1)}"
        holder = conn.execute(select(member.c.name).where(member.c.id == member_id)).scalar_one()
        conn.execute(
            insert(refund_account).values(
                member_id=member_id,
                bank_name=rng.choice(BANKS),
                account_holder=holder,
                account_number_enc=cipher.encrypt(number, refund_account_context(member_id)),
                account_last4=number[-4:],
            )
        )
    return True


INQUIRY_COUNT = 40
INQUIRY_TEMPLATES = (
    ("배송 문의", "주문한 상품이 언제 도착하는지 궁금합니다."),
    (
        "환불 요청",
        "상품이 마음에 들지 않아 환불을 요청드립니다. 환불계좌는 마이페이지에 등록했습니다.",
    ),
    ("상품 불량", "받은 상품에 흠집이 있습니다. 교환이 가능할까요?"),
    ("회원정보 변경", "주소가 바뀌었는데 마이페이지에서 수정하면 이미 주문한 건에도 반영되나요?"),
    ("결제 오류", "결제 중 오류가 났는데 카드 승인 문자는 왔습니다. 확인 부탁드립니다."),
)
INQUIRY_ANSWER = "문의 주셔서 감사합니다. 확인 후 처리해 드렸습니다. (가상 답변)"


def seed_inquiries(conn: Connection) -> bool:
    """가상 1:1 문의 (기능 레이어 7 ②) — 문의가 하나도 없을 때만. 절반 남짓은 CS가 답변한 상태

    문의 내용은 고정 문구라 개인정보가 없다. 답변자는 시드 CS 취급자(cs_kim·cs_choi).
    """
    if conn.execute(select(func.count()).select_from(inquiry)).scalar_one():
        return False
    members = (
        conn.execute(
            select(member.c.id)
            .where(member.c.email.like("user%@example.com"))
            .order_by(member.c.id)
        )
        .scalars()
        .all()
    )
    cs = (
        conn.execute(select(operator.c.id).where(operator.c.team == "CS").order_by(operator.c.id))
        .scalars()
        .all()
    )
    if not members or not cs:
        return False

    rng = random.Random(SEED + 3)  # noqa: S311 — 가짜 문의 분포용, 보안 용도 아님
    now = conn.execute(select(func.now())).scalar_one()
    for n in range(INQUIRY_COUNT):
        title, body = rng.choice(INQUIRY_TEMPLATES)
        created_at = now - timedelta(seconds=rng.randint(3600, 60 * 24 * 3600))
        answered = n % 3 != 0  # 3건 중 2건 답변 완료, 1건 대기
        conn.execute(
            insert(inquiry).values(
                member_id=rng.choice(members),
                title=title,
                body=body,
                status="ANSWERED" if answered else "OPEN",
                answer=INQUIRY_ANSWER if answered else None,
                answered_by=rng.choice(cs) if answered else None,
                created_at=created_at,
                answered_at=created_at + timedelta(hours=rng.randint(1, 48)) if answered else None,
            )
        )
    return True


# 정보보호 담당자의 플랫폼 계정 — Argus 담당자와 **같은 아이디** (2026-10-02 사용자 결정:
# 플랫폼 백오피스 아이디 = Argus 아이디). 소명의 관련 티켓 링크로 플랫폼 문의를 열어 볼 때 쓴다.
# 이 계정의 플랫폼 열람도 접속기록으로 Argus에 남는다 — 감시자도 감시된다.
# 기준선 시드(평소 조회 패턴)에는 넣지 않는다 — 담당자는 일상적으로 회원을 조회하지 않는다
OFFICER_OPERATOR = ("officer", "윤서진", "OPS", "ADMIN")


def ensure_officer_operator(conn: Connection, operator_password: str) -> bool:
    """담당자 플랫폼 계정이 없으면 만든다 — seed()와 따로 매번(이미 시드된 개발 스택에도 생기게)"""
    login_id, name, team, role = OFFICER_OPERATOR
    exists_ = conn.execute(select(operator.c.id).where(operator.c.login_id == login_id)).first()
    if exists_ is not None:
        return False
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
    record_initial_grant(conn, row)
    # 다른 취급자와 같이 Argus 명부로 동기화 — 같은 아이디의 Argus 담당자 계정과 이어진다
    enqueue(conn, "HANDLER", handler_event("HANDLER_CREATED", row, row.created_at))
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
            backfilled = backfill_consents(conn)
            commerce = seed_commerce(conn, FieldCipher(settings.require_payment_key()))
            inquiries = seed_inquiries(conn)
            officer = ensure_officer_operator(conn, password)
    finally:
        engine.dispose()

    if created:
        logger.info("seeded %d members, %d operators", MEMBER_COUNT, len(SEED_OPERATORS))
    else:
        logger.info("already seeded — skipped")
    if backfilled:
        logger.info("backfilled consent history for %d members", backfilled)
    if commerce:
        logger.info("seeded %d orders, %d refund accounts", ORDER_COUNT, REFUND_ACCOUNT_COUNT)
    if inquiries:
        logger.info("seeded %d inquiries", INQUIRY_COUNT)
    if officer:
        logger.info("created privacy officer operator: %s", OFFICER_OPERATOR[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
