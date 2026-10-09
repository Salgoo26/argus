"""v0.1 보강 2차 E2E — 화면 서버(Next.js) 경유 HTTP로만 한다 (브라우저와 같은 경로)

- I: 점검 보고서의 원장 기준점(마지막 id·해시·건수)과 직전 보고서 대조
- J: 탐지건의 처리 성격(데이터 유형·행위 구분)과 소명 제출 뒤 추가 기록 수
- K: 접속지·특정 회원 기반 기본 룰
- L: 플랫폼 역할별 접근 범위, 계정 부여(임시 비밀번호·변경 강제)·권한 이력
- L-4: Argus 계정 관리(담당자 전용)·계정 이력
- N: 보호 대상 등록부 — 게이트웨이의 DB 구조 목록 수신, 원장의 등록부 버전
"""

import os
import re
from datetime import datetime, timedelta, timezone

import psycopg

from conftest import PLATFORM_URL, Browser, argus_login, platform_login, wait_until

KST = timezone(timedelta(hours=9))


def test_report_keeps_ledger_anchor_and_checks_the_previous_one(officer):
    today = datetime.now(KST).date()
    period = {"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}
    browser = argus_login(*officer)

    first = browser.post("/api/reports", json=period)
    second = browser.post("/api/reports", json=period)
    assert first.status_code == 201 and second.status_code == 201, second.text

    anchor = first.json()["summary"]["integrity"]
    assert anchor["total"] > 0 and anchor["last_id"] > 0
    assert len(anchor["last_hash"]) == 64
    assert second.json()["summary"]["integrity"]["previous"] == {
        "status": "MATCH",
        "report_id": first.json()["id"],
        "last_id": anchor["last_id"],
    }
    browser.close()


def test_detections_carry_nature_and_after_submission_count(officer):
    # J — 대량 다운로드 탐지건은 "회원 기본정보 · 내려받기" 성격, 제출 뒤 기록 수가 함께 온다
    browser = argus_login(*officer)
    items = browser.post("/api/detections/search", json={"size": 100}).json()["items"]
    assert items and all("after_submission_count" in c for c in items)
    downloads = [c for c in items if c["rule_name"] == "대량 다운로드"]
    assert downloads and {(c["data_category"], c["action_group"]) for c in downloads} == {
        ("MEMBER_BASIC", "DOWNLOAD")
    }
    browser.close()


def test_ip_and_subject_rules_are_seeded(officer):
    # K — 접속지·특정 회원 기반 기본 룰 3개가 룰 빌더 목록에 있다
    browser = argus_login(*officer)
    names = {r["name"] for r in browser.get("/api/rules").json()["items"]}
    assert {"허용 범위 밖 접속지", "짧은 시간 여러 접속지", "특정 회원 반복 처리"} <= names
    browser.close()


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


def test_marketing_cannot_open_orders_and_the_refusal_reaches_argus():
    # L-1 — 마케팅 역할은 주문 화면 403, 거부된 시도도 접속기록(FAILURE)으로 Argus에 간다
    marketing = platform_login("mkt_lee")
    assert marketing.get("/api/admin/orders").status_code == 403
    me = marketing.get("/api/admin/auth/me").json()
    assert "ORDERS" not in me["permissions"] and "MEMBERS" in me["permissions"]

    rows = wait_until(
        "마케팅의 주문 조회 거부 기록 원장 도착",
        lambda: (
            _ledger(
                "SELECT result FROM access_log a JOIN source_system s ON s.id = a.source_system_id"
                " WHERE s.code = 'PLATFORM' AND actor_login_id = %s AND data_category = 'ORDER'"
                " AND result = 'FAILURE'",
                ("mkt_lee",),
            )
            or None
        ),
    )
    assert ("FAILURE",) in rows


def test_admin_grants_an_account_with_a_temporary_password():
    # L-2·L-3 — 계정을 만들면 임시 비밀번호(한 번만) → 첫 로그인 때 변경 강제, 이력에 사유
    admin = platform_login("admin_han")
    created = admin.post(
        "/api/admin/accounts",
        json={
            "login_id": "e2e_cs_new",
            "name": "가상상담",
            "team": "CS",
            "role": "CS",
            "reason": "E2E — CS팀 신규 입사",
        },
    )
    assert created.status_code == 201, created.text
    temp = created.json()["temporary_password"]

    newbie = Browser(PLATFORM_URL, "platform_session")
    login = newbie.post("/api/admin/auth/login", json={"login_id": "e2e_cs_new", "password": temp})
    assert login.json()["must_change_password"] is True
    assert newbie.get("/api/admin/members").status_code == 403
    changed = newbie.post(
        "/api/admin/auth/password",
        json={"current_password": temp, "new_password": "e2e-changed-pass-1"},
    )
    assert changed.status_code == 204
    assert newbie.get("/api/admin/inquiries").status_code == 200  # 상담 역할 — 문의 가능
    assert newbie.get("/api/admin/members/export").status_code == 403  # 다운로드는 불가

    history = admin.post(
        "/api/admin/accounts/history/search", json={"login_id": "e2e_cs_new"}
    ).json()["items"]
    assert [(h["change_type"], h["reason"], h["actor_login_id"]) for h in history] == [
        ("GRANT", "E2E — CS팀 신규 입사", "admin_han")
    ]
    assert temp not in str(history)


def test_argus_account_management_is_officer_only_and_recorded(officer, handler_password):
    # L-4 — 담당자는 계정 목록·이력을 보고, 스크립트로 만든 담당자 계정도 사유와 함께 이력에 있다
    browser = argus_login(*officer)
    users = {u["login_id"]: u for u in browser.get("/api/users").json()["items"]}
    assert users[officer[0]]["role"] == "OFFICER"
    assert users["ops_park"]["handler"]["login_id"] == "ops_park"
    history = browser.post("/api/users/history/search", json={"login_id": officer[0]}).json()
    assert [(h["change_type"], h["reason"]) for h in history["items"]] == [
        ("GRANT", "E2E 담당자 계정")
    ]
    browser.close()

    handler = argus_login("ops_park", handler_password("ops_park"))
    assert handler.get("/api/users").status_code == 403
    handler.close()


def test_gateway_sends_db_structure_and_records_registry_version(officer):
    # N — 게이트웨이가 플랫폼 DB 구조(이름·자료형만)를 보내고, 등록부 버전을 원장에 남긴다
    browser = argus_login(*officer)

    def received():
        data = browser.get("/api/protection").json()
        return data if data["summary"]["last_schema_at"] else None

    data = wait_until("게이트웨이의 DB 구조 목록 수신", received)
    tables = {t["table_name"]: t for t in data["tables"]}
    assert tables["member"]["data_category"] == "MEMBER_BASIC"  # 초기 등록(고정 표 이관)
    # 고정 표에 없던 직원 계정 테이블은 미분류로 드러난다 — 담당자가 분류할 대상
    assert {c["status"] for c in tables["operator"]["columns"]} == {"UNCLASSIFIED"}
    assert data["summary"]["unclassified"] > 0
    assert "@" not in str(data)  # 구조 목록에 데이터 값(이메일 등)이 없다
    browser.close()

    versions = _ledger(
        "SELECT DISTINCT a.context ->> 'registry_version' FROM access_log a"
        " WHERE a.access_path = 'DB' AND a.context ? 'sql_normalized'"
        " AND a.context ? 'registry_version'",
        (),
    )
    # 게이트웨이를 거친 문장마다 판정에 쓴 등록부 버전 (기준선 시드 기록은 버전 없음)
    assert versions and all(re.fullmatch(r"builtin|r\d+", v) for (v,) in versions)
