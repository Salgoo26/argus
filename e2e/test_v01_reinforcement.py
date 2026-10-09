"""v0.1 보강 E2E — 로그아웃 기록(A)·취급자 "내 접속기록"(D)·탐지건 검색(C-1)

화면 서버(Next.js) 경유 HTTP로만 한다 — 브라우저와 같은 경로.
"""

import os
import re

import psycopg

from conftest import argus_login, platform_login, wait_until

MASKED = re.compile(r"^member_\d{2}\*\*\*$")


def _ledger(query: str, params: tuple) -> list[tuple]:
    """운영자 확인 — Argus 앱 계정으로 원장 조회 (SELECT만 가능한 계정)"""
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ["ARGUS_DB_HOST"],
        dbname=os.environ["ARGUS_DB_NAME"],
        user=os.environ["ARGUS_DB_USER"],
        password=os.environ["ARGUS_DB_PASSWORD"],
    )
    with psycopg.connect(conninfo) as conn:
        return conn.execute(query, params).fetchall()


def test_platform_logout_reaches_the_ledger():
    admin = platform_login("mkt_lee")
    assert admin.post("/api/admin/auth/logout").status_code == 204
    # 로그아웃 뒤에는 세션이 없다 — 기록하느라 연장되지 않는다
    assert admin.get("/api/admin/members").status_code == 401

    rows = wait_until(
        "플랫폼 LOGOUT 원장 도착",
        lambda: (
            _ledger(
                "SELECT result, data_category, subject_type FROM access_log a"
                " JOIN source_system s ON s.id = a.source_system_id"
                " WHERE s.code = 'PLATFORM' AND actor_login_id = %s AND action = 'LOGOUT'",
                ("mkt_lee",),
            )
            or None
        ),
    )
    assert rows[0] == ("SUCCESS", "NONE", None)  # 정보주체 없음


def test_handler_sees_only_own_access_logs(handler_password):
    # ops_park이 회원 목록을 보고(READ), mkt_lee도 본다 — ops_park에게는 자기 기록만
    for login_id in ("ops_park", "mkt_lee"):
        assert platform_login(login_id).get("/api/admin/members").status_code == 200
    wait_until(
        "두 사람의 회원 목록 조회 원장 도착",
        lambda: (
            len(
                _ledger(
                    "SELECT DISTINCT actor_login_id FROM access_log"
                    " WHERE request_path = '/admin/members' AND actor_login_id IN (%s, %s)",
                    ("ops_park", "mkt_lee"),
                )
            )
            == 2
            or None
        ),
    )

    handler = argus_login("ops_park", handler_password("ops_park"))
    res = handler.post("/api/access-logs/search", json={})
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert items and {i["actor_login_id"] for i in items} == {"ops_park"}
    # 정보주체는 마스킹 값만 — 원본 회원번호가 응답에 없다
    subjects = [s for i in items for s in i["subjects"]]
    assert subjects and all(MASKED.match(s) for s in subjects)
    assert not re.search(r'"1\d{4}"', res.text)

    # 남의 아이디·Argus 자체 기록은 거부
    assert handler.post("/api/access-logs/search", json={"actor": "mkt_lee"}).status_code == 403
    assert handler.post("/api/access-logs/search", json={"source": "ARGUS"}).status_code == 403


def test_detection_search_takes_conditions_in_the_body(officer):
    officer_browser = argus_login(*officer)
    res = officer_browser.post(
        "/api/detections/search", json={"actor": "ops_park", "sort": "SEVERITY", "size": 5}
    )
    assert res.status_code == 200, res.text
    assert all(i["actor_login_id"] == "ops_park" for i in res.json()["items"])
    # 검색 조건은 키 이름만 Argus 자체 기록에 남는다 — 아이디 값은 남지 않는다
    [(keys,)] = _ledger(
        "SELECT request_query_keys FROM access_log WHERE request_path = '/api/detections/search'"
        " AND actor_login_id = %s ORDER BY id DESC LIMIT 1",
        (officer[0],),
    )
    assert keys == ["actor", "size", "sort"]


# ── 플랫폼 관리자 검색 (v0.1 보강 E) ─────────────────────────

# scripts/e2e.sh가 모든 컨테이너 로그에서 이 값을 찾는다 — 검색어가 서버 로그에 남으면 실패
SEARCH_NEEDLE = "e2e-search-needle-7q"


def test_platform_member_search_records_shown_members_not_search_values():
    admin = platform_login("ops_park")
    res = admin.post("/api/admin/members/search", json={"email": "user00", "size": 50})
    assert res.status_code == 200, res.text
    shown = sorted(str(m["id"]) for m in res.json()["items"])
    assert shown  # 시드 회원 user0001~ 가 걸린다
    # 검색어를 바꿔 0건도 남는지 — 이 값은 서버 로그에도 없어야 한다(scripts/e2e.sh)
    empty = admin.post("/api/admin/members/search", json={"name": SEARCH_NEEDLE})
    assert empty.status_code == 200 and empty.json()["items"] == []

    def arrived():
        rows = _ledger(
            "SELECT subject_ids, subject_count, request_query_keys FROM access_log"
            " WHERE request_path = '/admin/members/search' AND actor_login_id = %s"
            " ORDER BY id DESC LIMIT 2",
            ("ops_park",),
        )
        return rows if len(rows) == 2 else None

    zero, found = wait_until("검색 접속기록 원장 도착", arrived)
    # 결과로 보인 회원 PK 전부가 정보주체 (안내서 129쪽 누락 사례 ② — 공란이 아니다)
    assert sorted(found[0]) == shown and found[1] == len(shown)
    assert found[2] == ["email", "size"]  # 키 이름만
    assert zero[1] == 0 and zero[2] == ["name"]
    everything = str(_ledger("SELECT request_query_keys, context FROM access_log", ()))
    assert SEARCH_NEEDLE not in everything and "user00" not in everything
