"""회원 목록·CSV 다운로드 — 시나리오의 "120건 다운로드", 필터, CSV 수식 주입 방어"""

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from app.models import member

from conftest import add_operator, login

KST_MIDNIGHT_0915 = datetime(2026, 9, 15, tzinfo=UTC) - timedelta(hours=9)  # 2026-09-15 00:00 KST


def _add_members(engine, count: int, **overrides) -> None:
    rows = [
        {
            "email": f"user{n:04d}@example.com",
            "password_hash": "unusable",
            "name": f"회원{n}",  # 가상
            "phone": f"010-0000-{n:04d}",
            "created_at": KST_MIDNIGHT_0915 + timedelta(hours=n),
        }
        | overrides
        for n in range(1, count + 1)
    ]
    with engine.begin() as conn:
        conn.execute(insert(member), rows)


@pytest.fixture
def logged_in(client, engine, password_hash):
    add_operator(engine, password_hash)
    assert login(client).status_code == 200
    return client


def _csv_rows(res) -> list[list[str]]:
    text = res.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


# ── 목록 ──────────────────────────────────────────────────


def test_list_members_is_paginated(logged_in, engine):
    _add_members(engine, 45)

    res = logged_in.get("/admin/members", params={"page": 3, "size": 20})

    body = res.json()
    assert res.status_code == 200
    assert body["total"] == 45 and body["page"] == 3
    assert [m["email"] for m in body["items"]] == [
        f"user{n:04d}@example.com" for n in (41, 42, 43, 44, 45)
    ]
    assert "password_hash" not in body["items"][0]


@pytest.mark.parametrize("params", [{"page": 0}, {"size": 101}, {"size": "many"}])
def test_list_rejects_bad_paging(logged_in, params):
    assert logged_in.get("/admin/members", params=params).status_code == 400


# ── CSV 다운로드 ──────────────────────────────────────────


def test_export_120_members_as_csv(logged_in, engine):
    _add_members(engine, 150)

    res = logged_in.get("/admin/members/export", params={"limit": 120})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["content-disposition"].startswith('attachment; filename="members_')
    assert res.headers["cache-control"] == "no-store"
    assert res.content.startswith(b"\xef\xbb\xbf")  # 엑셀 한글 인식용 BOM
    rows = _csv_rows(res)
    assert rows[0] == [
        "id",
        "name",
        "email",
        "phone",
        "joined_at",
    ]  # 주소는 배송지 — 회원 파일에 없음
    assert len(rows) == 1 + 120
    assert rows[1][2] == "user0001@example.com"
    assert rows[1][4].endswith("+09:00")  # 가입일은 한국 시각으로


def test_export_filters_by_joined_date_in_kst(logged_in, engine):
    # 회원 n은 2026-09-15 00:00 KST + n시간에 가입 → 9/15에는 1~23번, 9/16에는 24~47번
    _add_members(engine, 60)

    res = logged_in.get(
        "/admin/members/export", params={"joined_from": "2026-09-16", "joined_to": "2026-09-16"}
    )

    emails = [row[2] for row in _csv_rows(res)[1:]]
    assert emails == [f"user{n:04d}@example.com" for n in range(24, 48)]


def test_export_excludes_withdrawn_members(logged_in, engine):
    _add_members(engine, 3)
    with engine.begin() as conn:
        conn.execute(
            insert(member).values(
                email="gone@example.com",
                password_hash="unusable",
                name="탈퇴회원",
                status="WITHDRAWN",
                withdrawn_at=datetime.now(UTC),
            )
        )
    emails = [row[2] for row in _csv_rows(logged_in.get("/admin/members/export"))[1:]]
    assert "gone@example.com" not in emails and len(emails) == 3


def test_export_neutralizes_formula_cells(logged_in, engine):
    # 회원이 이름에 수식을 넣어 가입 → 관리자가 엑셀로 열면 실행되는 공격 (CSV Injection)
    with engine.begin() as conn:
        conn.execute(
            insert(member).values(
                email="evil@example.com",
                password_hash="unusable",
                name='=HYPERLINK("http://attacker.example","click")',
                phone="-2",
            )
        )
    [row] = _csv_rows(logged_in.get("/admin/members/export"))[1:]
    assert row[1].startswith("'=") and row[3] == "'-2"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 10_001}, {"joined_from": "2026-13-01"}])
def test_export_rejects_bad_params(logged_in, params):
    assert logged_in.get("/admin/members/export", params=params).status_code == 400


def test_export_requires_login(client):
    assert client.get("/admin/members/export").status_code == 401


def test_export_refreshes_session_cookie(logged_in, engine):
    # 핸들러가 Response를 직접 돌려줘도 만료 연장 쿠키가 빠지지 않는다
    _add_members(engine, 1)
    res = logged_in.get("/admin/members/export")
    assert "platform_session=" in res.headers["set-cookie"]
