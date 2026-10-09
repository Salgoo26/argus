"""v0.1 보강 2차 E2E — 화면 서버(Next.js) 경유 HTTP로만 한다 (브라우저와 같은 경로)

- I: 점검 보고서의 원장 기준점(마지막 id·해시·건수)과 직전 보고서 대조
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
