"""E2E 시나리오 공통 도구 — 브라우저 흉내, 관리 명령, 기다리기

실행: scripts/e2e.sh (전체 스택을 별도 프로젝트로 새로 띄운 뒤 e2e 컨테이너에서 pytest)

두 종류의 행위자가 있다.
- 사람(취급자·담당자): 화면 서버(Next.js) 경유 HTTP만 쓴다 — 브라우저와 같은 API·경로
- 운영자: README에 안내한 관리 명령(python -m app.scripts.users, app.worker --once,
  app.scripts.verify_chain)을 그대로 실행한다
"""

import os
import random
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

PLATFORM_URL = os.environ["E2E_PLATFORM_URL"]
ARGUS_URL = os.environ["E2E_ARGUS_URL"]
OPERATOR_PASSWORD = os.environ["E2E_OPERATOR_PASSWORD"]

WAIT_TIMEOUT_SEC = 90


class Browser:
    """한 사람의 브라우저 탭 — 화면 서버 하나와 그 세션 쿠키 하나.

    세션 쿠키는 Secure라 일반 HTTP 클라이언트의 쿠키 저장소는 http:// 요청에 싣지 않는다
    (브라우저는 localhost만 예외로 허용). 그래서 쿠키를 직접 들고 다닌다.
    요청마다 재발급(30분 미사용 만료, policy 4-3)되므로 매 응답에서 갱신한다.
    """

    def __init__(self, base_url: str, cookie_name: str):
        self.http = httpx2.Client(base_url=base_url, timeout=30)
        self.cookie_name = cookie_name
        self.token: str | None = None

    def request(self, method: str, path: str, **kwargs) -> httpx2.Response:
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Cookie"] = f"{self.cookie_name}={self.token}"
        response = self.http.request(method, path, headers=headers, **kwargs)
        self.http.cookies.clear()  # 쿠키는 위에서 직접 관리 (이중 관리 방지)
        for raw in response.headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            cookie.load(raw)
            if self.cookie_name in cookie:
                self.token = cookie[self.cookie_name].value or None
        return response

    def get(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self.http.close()


def shop_signup(
    shop: Browser, email: str, password: str, name: str, phone: str, consents: dict[str, bool]
) -> tuple[httpx2.Response, str]:
    """고객 가입 (커머스 확장) — 이메일 인증 → 가상 휴대폰 본인확인 → 가입. (응답, 아이디)

    주민번호 앞 7자리는 가상 본인확인 창(브라우저) 안에서만 쓰여 API에는 보내지 않는다.
    생년월일은 매번 달리 — 같은 사람(CI)으로 겹쳐 1인 1계정 검사에 걸리지 않게.
    """
    login_id = "e2e" + secrets.token_hex(4)
    verified = shop.post("/api/shop/verifications/email", json={"email": email})
    assert verified.status_code == 200, verified.text
    rnd = random.Random()  # noqa: S311 — 가상 생년월일, 보안 용도 아님
    birth = f"{rnd.randint(1970, 2000)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
    identity = shop.post(
        "/api/shop/verifications/identity",
        json={
            "carrier": "SKT",
            "name": name,
            "birth_date": birth,
            "gender": "F",
            "is_foreigner": False,
            "phone": phone,
            "agreed": True,
        },
    )
    assert identity.status_code == 200, identity.text
    joined = shop.post(
        "/api/shop/auth/signup",
        json={
            "login_id": login_id,
            "password": password,
            "email_token": verified.json()["token"],
            "identity_token": identity.json()["token"],
            "consents": consents,
        },
    )
    return joined, login_id


PG_KEY = re.compile(r'action="/checkout/(pay_[0-9a-f]+)/approve"')


def buy(
    shop: Browser,
    product_id: int,
    address_id: int,
    *,
    card_number: str = "1234567890123452",
    memo: str | None = None,
) -> dict:
    """주문 → 가상 PG 결제창 → 승인 (커머스 확장 F6). 승인된 주문을 돌려준다.

    브라우저가 하는 일을 그대로 한다: 주문(결제대기) → 결제창에 주문·구매자 정보 POST →
    카드 입력(결제창, 즉 PG에만) → PG가 돌려보낸 주소의 paymentKey로 쇼핑몰에 승인 요청.
    돌아갈 주소의 결과 페이지는 이 값을 주문 화면에 넘기기만 하므로 여기서는 열지 않는다.
    """
    created = shop.post(
        "/api/shop/orders",
        json={"product_id": product_id, "shipping_address_id": address_id, "delivery_memo": memo},
    )
    assert created.status_code == 201, created.text
    order = created.json()["order"]
    request = created.json()["payment_request"]
    back = f"{PLATFORM_URL}/checkout/result?order={order['id']}"
    with httpx2.Client(timeout=30) as pg:
        page = pg.post(
            request["checkout_url"],
            data={**request["fields"], "successUrl": back, "failUrl": f"{back}&fail=1"},
        )
        assert page.status_code == 200, page.text
        [payment_key] = PG_KEY.findall(page.text)
        base = request["checkout_url"].rsplit("/checkout", 1)[0]
        approved = pg.post(
            f"{base}/checkout/{payment_key}/approve",
            data={"card_company": "하늘카드", "card_number": card_number, "installment": "0"},
        )
    assert approved.status_code == 303, approved.text
    result = {k: v[0] for k, v in parse_qs(urlsplit(approved.headers["location"]).query).items()}
    assert result["paymentKey"] == payment_key and result["order"] == str(order["id"])
    paid = shop.post(
        f"/api/shop/orders/{order['id']}/payment",
        json={"payment_key": payment_key, "amount": int(result["amount"])},
    )
    assert paid.status_code == 200, paid.text
    return paid.json()


def platform_login(login_id: str, password: str = OPERATOR_PASSWORD) -> Browser:
    browser = Browser(PLATFORM_URL, "platform_session")
    response = browser.post(
        "/api/admin/auth/login", json={"login_id": login_id, "password": password}
    )
    assert response.status_code == 200, response.text
    return browser


def create_operator(login_id: str, name: str, team: str, role: str) -> str:
    """관리자(admin_han)가 계정·권한 화면으로 새 관리자 계정을 만든다 (v0.1 보강 L-2).
    임시 비밀번호로 처음 로그인해 새 비밀번호로 바꾸고 그 비밀번호를 돌려준다
    (platform_login에 넘김).
    시드 계정 역할을 바꾸지 않고 역할 표에 맞는 계정을 쓰기 위함"""
    password = secrets.token_hex(8) + "-Aa1"  # 비밀번호 규칙(10자·두 종류 이상)을 늘 만족
    admin = platform_login("admin_han")
    created = admin.post(
        "/api/admin/accounts",
        json={"login_id": login_id, "name": name, "team": team, "role": role, "reason": "E2E"},
    )
    admin.close()
    assert created.status_code == 201, created.text
    first = Browser(PLATFORM_URL, "platform_session")
    temp = created.json()["temporary_password"]
    assert first.post(
        "/api/admin/auth/login", json={"login_id": login_id, "password": temp}
    ).json()["must_change_password"]
    changed = first.post(
        "/api/admin/auth/password",
        json={"current_password": temp, "new_password": password},
    )
    first.close()
    assert changed.status_code == 204, changed.text
    return password


def argus_login(login_id: str, password: str) -> Browser:
    browser = Browser(ARGUS_URL, "argus_session")
    response = browser.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert response.status_code == 200, response.text
    return browser


# ── 운영자 관리 명령 ─────────────────────────────────────────


def admin_command(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """argus-api 이미지의 관리 명령 실행 — docker compose exec argus-api python -m ... 와 같다"""
    return subprocess.run(  # noqa: S603 — 고정된 모듈 이름과 테스트가 만든 인자만 넘긴다
        [sys.executable, "-m", *args],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def run_detection_batch() -> None:
    """탐지 배치 즉시 1회 — 다른 순찰과 겹치면 건너뛰므로(skipped) 실패로 보지 않는다"""
    result = admin_command("app.worker", "--once")
    assert result.returncode == 0 or "skipped" in result.stderr, result.stderr


def wait_until[T](what: str, probe: Callable[[], T | None], interval: float = 2.0) -> T:
    """probe가 값을 돌려줄 때까지 기다린다 — relay(2초 주기)·탐지 배치처럼 비동기로 흐르는 구간용"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while True:
        value = probe()
        if value is not None:
            return value
        if time.monotonic() > deadline:
            pytest.fail(f"{WAIT_TIMEOUT_SEC}초 안에 {what} — 실패")
        time.sleep(interval)


def _set_argus_password(login_id: str, password: str) -> bool:
    result = admin_command(
        "app.scripts.users",
        "set-password",
        login_id,
        "--password-env",
        "E2E_NEW_PASSWORD",
        env={"E2E_NEW_PASSWORD": password},
    )
    return result.returncode == 0


# ── 계정 준비 (세션당 한 번) ─────────────────────────────────


@pytest.fixture(scope="session")
def officer() -> tuple[str, str]:
    """정보보호 담당자 계정 — 매 실행마다 무작위 ID·비밀번호로 발급 (레포에 비밀번호 없음)"""
    login_id = f"e2e_officer_{secrets.token_hex(3)}"
    password = secrets.token_urlsafe(18)
    result = admin_command(
        "app.scripts.users",
        "create-officer",
        login_id,
        "--password-env",
        "E2E_NEW_PASSWORD",
        "--reason",
        "E2E 담당자 계정",
        env={"E2E_NEW_PASSWORD": password},
    )
    assert result.returncode == 0, result.stderr
    return login_id, password


@pytest.fixture(scope="session")
def handler_password() -> Callable[[str], str]:
    """취급자의 Argus 비밀번호 설정 — 계정 자체는 플랫폼 시드 → outbox → relay 동기화로 생긴다(②).
    동기화가 도착할 때까지 기다린 뒤 무작위 비밀번호를 설정한다."""
    issued: dict[str, str] = {}

    def issue(login_id: str) -> str:
        if login_id not in issued:
            password = secrets.token_urlsafe(18)
            wait_until(
                f"취급자 {login_id}의 Argus 계정 동기화",
                lambda: True if _set_argus_password(login_id, password) else None,
            )
            issued[login_id] = password
        return issued[login_id]

    return issue
