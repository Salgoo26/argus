"""점검 보고서 E2E (기능 레이어 9) — 담당자가 이번 달 보고서를 만든다

화면 서버 경유 API로 만들고, 경로별 섹션·해시체인 점검 결과·마스킹(원본 회원번호 없음)을 확인한다.
보고서 생성은 Argus 자체 접속기록(EXPORT)으로 남는다.
"""

import re
from datetime import datetime, timedelta, timezone

from conftest import argus_login

KST = timezone(timedelta(hours=9))
RAW_MEMBER_ID = re.compile(r"member_1\d{4}\b|\"1\d{4}\"")  # 마스킹되지 않은 회원번호 표기


def test_officer_creates_monthly_report(officer):
    today = datetime.now(KST).date()
    browser = argus_login(*officer)
    created = browser.post(
        "/api/reports",
        json={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()},
    )
    assert created.status_code == 201, created.text
    report = created.json()
    summary = report["summary"]
    assert set(summary["paths"]) == {"APP", "DB"}  # 전체 = 경로별 섹션
    assert summary["integrity"]["ok"] is True  # §8③ 원장 해시체인
    assert summary["paths"]["APP"]["logs"]["total"] > 0
    assert not RAW_MEMBER_ID.search(created.text)  # 마스킹 보고서만 (policy 6-1)

    again = browser.get(f"/api/reports/{report['id']}")
    assert again.status_code == 200 and again.json()["summary"] == summary
    browser.close()
