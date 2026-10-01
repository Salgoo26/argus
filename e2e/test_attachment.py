"""소명 근거자료 첨부 E2E (기능 레이어 7 ③)

취급자는 다른 시나리오와 겹치지 않는 admin_han — 같은 날·같은 룰·같은 취급자는 탐지건 하나로 묶인다.

admin_han가 회원 60명 다운로드 → 대량 다운로드 탐지 + 자동 소명 요청 → admin_han가 Argus 화면 경유로
결재 문서(PDF)를 첨부하고 제출 → 담당자가 내려받아 SHA-256 일치 확인 → 다운로드가 Argus 자체
접속기록으로 원장에 남았는지 확인. 형식 위장(.png 이름의 HTML)은 거부.
"""

import hashlib

from conftest import argus_login, platform_login, run_detection_batch, wait_until

PDF = b"%PDF-1.4\n% E2E virtual approval document\n" + b"0" * 256


def test_attachment_upload_submit_and_download(officer, handler_password):
    password = handler_password("admin_han")  # 탐지 전에 계정이 있어야 자동 소명 요청이 간다
    platform = platform_login("admin_han")
    assert platform.get("/api/admin/members/export", params={"limit": 60}).status_code == 200

    officer_browser = argus_login(*officer)

    def probe():
        run_detection_batch()
        items = officer_browser.get("/api/detections", params={"size": 100}).json()["items"]
        return next(
            (
                i
                for i in items
                if i["rule_name"] == "대량 다운로드"
                and i["actor_login_id"] == "admin_han"
                and i["subject_count_sum"] == 60
            ),
            None,
        )

    case = wait_until("admin_han의 대량 다운로드 탐지건", probe)
    assert case["status"] == "REQUESTED"
    case_id = case["id"]

    # ── 취급자: 첨부(형식 위장은 거부) → 제출 ───────────────────
    handler = argus_login("admin_han", password)
    disguised = handler.post(
        f"/api/detections/{case_id}/attachments",
        files={"file": ("capture.png", b"<html><script>alert(1)</script>", "image/png")},
    )
    assert disguised.status_code == 415
    uploaded = handler.post(
        f"/api/detections/{case_id}/attachments",
        files={"file": ("운영팀_결재.pdf", PDF, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["sha256"] == hashlib.sha256(PDF).hexdigest()
    submitted = handler.post(
        f"/api/detections/{case_id}/submit", json={"content": "이벤트 발송 대상 추출 — 결재 첨부"}
    )
    assert submitted.status_code == 200

    # ── 담당자: 차수의 첨부 확인 → 내려받기(해시 일치) ─────────────
    detail = officer_browser.get(f"/api/detections/{case_id}").json()
    [att] = detail["explanations"][0]["attachments"]
    assert att["original_name"] == "운영팀_결재.pdf"
    res = officer_browser.get(f"/api/detections/{case_id}/attachments/{att['id']}")
    assert res.status_code == 200 and res.content == PDF
    assert res.headers["content-disposition"].startswith("attachment;")

    # 제출 뒤에는 바꿀 수 없다
    assert (
        handler.request("DELETE", f"/api/detections/{case_id}/attachments/{att['id']}").status_code
        == 409
    )

    # ── 원장: 담당자의 첨부 다운로드가 Argus 자체 접속기록으로 ─────
    login_id = officer[0]
    found = officer_browser.post(
        "/api/access-logs/search", json={"actor": login_id, "source": "ARGUS", "size": 100}
    ).json()["items"]
    paths = [i["request_path"] for i in found]
    assert "/api/detections/{detection_id}/attachments/{attachment_id}" in paths
