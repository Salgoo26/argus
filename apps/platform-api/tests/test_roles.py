"""역할별 접근 범위 (v0.1 보강 L-1 — 고시 §5① 최소 권한)

| 기능                     | ADMIN | OPS | CS | MARKETING |
| 회원 목록·상세·검색      |  O    |  O  | O  |  O        |
| 주문·1:1 문의(답변 포함) |  O    |  O  | O  |  -        |
| 회원 CSV 다운로드        |  O    |  O  | -  |  -        |
| 환불계좌 전체 보기       |  O    |  O  | O  |  -        |
| DB 접속 토큰 발급        |  O    |  O  | -  |  -        |
| 계정·권한 관리           |  O    |  -  | -  |  -        |
"""

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import PERMISSIONS

from conftest import TEST_CLIENT_ADDR, add_operator, login, outbox_payloads, signup

ROLES = ("ADMIN", "OPS", "CS", "MARKETING")
TEAM_OF = {"ADMIN": "OPS", "OPS": "OPS", "CS": "CS", "MARKETING": "MARKETING"}

# (설명, 메서드, 경로, 본문, 허용 역할)
ROUTES = [
    ("회원 목록", "GET", "/admin/members", None, ROLES),
    ("회원 검색", "POST", "/admin/members/search", {"page": 1, "size": 20}, ROLES),
    ("회원 상세", "GET", "/admin/members/{member}", None, ROLES),
    ("주문 목록", "GET", "/admin/orders", None, ("ADMIN", "OPS", "CS")),
    ("주문 검색", "POST", "/admin/orders/search", {"page": 1, "size": 20}, ("ADMIN", "OPS", "CS")),
    ("문의 목록", "GET", "/admin/inquiries", None, ("ADMIN", "OPS", "CS")),
    ("문의 검색", "POST", "/admin/inquiries/search", {"page": 1}, ("ADMIN", "OPS", "CS")),
    ("회원 CSV", "GET", "/admin/members/export", None, ("ADMIN", "OPS")),
    (
        "환불계좌 전체",
        "GET",
        "/admin/members/{member}/refund-account",
        None,
        ("ADMIN", "OPS", "CS"),
    ),
    ("DB 토큰", "POST", "/admin/db-tokens", None, ("ADMIN", "OPS")),
    ("계정 목록", "GET", "/admin/accounts", None, ("ADMIN",)),
]


@pytest.fixture
def member_id(client) -> int:
    signup(client)
    client.cookies.clear()
    return 1


@pytest.fixture
def as_role(app, engine, password_hash):
    clients = []

    def make(role: str) -> TestClient:
        login_id = f"user_{role.lower()}"
        add_operator(engine, password_hash, login_id=login_id, team=TEAM_OF[role], role=role)
        c = TestClient(app, client=TEST_CLIENT_ADDR)
        clients.append(c)
        assert login(c, login_id).status_code == 200
        return c

    yield make
    for c in clients:
        c.close()


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("label", "method", "path", "body", "allowed"), ROUTES)
def test_role_access_matrix(as_role, member_id, role, label, method, path, body, allowed):
    c = as_role(role)
    res = c.request(method, path.format(member=member_id), json=body)
    if role in allowed:
        # 접근은 된다 — 대상이 없으면 404일 수 있다(환불계좌 미등록 회원)
        assert res.status_code not in (401, 403) and res.status_code < 500, res.text
    else:
        assert res.status_code == 403, f"{role} must not reach {label}"
        assert res.json()["error"]["code"] == "FORBIDDEN"


def test_permission_table_matches_the_design():
    assert {name: set(roles) for name, roles in PERMISSIONS.items()} == {
        "MEMBERS": set(ROLES),
        "ORDERS": {"ADMIN", "OPS", "CS"},
        "INQUIRIES": {"ADMIN", "OPS", "CS"},
        "MEMBER_EXPORT": {"ADMIN", "OPS"},
        "REFUND_FULL_VIEW": {"ADMIN", "OPS", "CS"},
        "DB_TOKEN": {"ADMIN", "OPS"},
        "ACCOUNTS": {"ADMIN"},
    }


def test_refused_request_is_recorded_as_failure(engine, as_role, member_id):
    # 권한이 없어 거부된 시도도 접속기록에 남는다 — 누가 무엇을 하려 했는지 (Agent 미들웨어 유지)
    marketing = as_role("MARKETING")
    assert marketing.get(f"/admin/members/{member_id}/refund-account").status_code == 403
    assert marketing.get("/admin/members/export").status_code == 403

    events = [e for e in outbox_payloads(engine) if e["actor"]["login_id"] == "user_marketing"]
    refused = [(e["action"], e["data_category"], e["result"]) for e in events[1:]]  # 0 = LOGIN
    assert refused == [("READ", "PAYMENT", "FAILURE"), ("DOWNLOAD", "MEMBER_BASIC", "FAILURE")]


def test_marketing_member_detail_has_no_orders(as_role, member_id):
    assert as_role("MARKETING").get(f"/admin/members/{member_id}").json()["orders"] is None
    assert as_role("CS").get(f"/admin/members/{member_id}").json()["orders"] == []


@pytest.mark.parametrize("role", ROLES)
def test_me_lists_permissions_for_the_menu(as_role, role):
    me = as_role(role).get("/admin/auth/me").json()
    assert set(me["permissions"]) == {name for name, roles in PERMISSIONS.items() if role in roles}
    assert me["must_change_password"] is False
