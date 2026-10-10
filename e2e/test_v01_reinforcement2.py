"""v0.1 보강 2차 E2E — 화면 서버(Next.js) 경유 HTTP로만 한다 (브라우저와 같은 경로)

- I: 점검 보고서의 원장 기준점(마지막 id·해시·건수)과 직전 보고서 대조
- J: 탐지건의 처리 성격(데이터 유형·행위 구분)과 소명 제출 뒤 추가 기록 수
"""

from datetime import datetime, timedelta, timezone

from conftest import argus_login

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
