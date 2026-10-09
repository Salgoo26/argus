"""소명 근거자료 첨부 E2E (기능 레이어 7 ③)

취급자는 다른 시나리오와 겹치지 않는 admin_han — 같은 날·같은 룰·같은 취급자는 탐지건 하나로 묶인다.

admin_han가 회원 60명 다운로드 → 대량 다운로드 탐지 + 자동 소명 요청 → admin_han가 Argus 화면 경유로
결재 문서(PDF)를 첨부하고 제출 → 담당자가 내려받아 SHA-256 일치 확인 → 다운로드가 Argus 자체
접속기록으로 원장에 남았는지 확인. 형식 위장(.png 이름의 HTML)은 거부.
"""

import hashlib
import secrets

from conftest import admin_command, argus_login, platform_login, run_detection_batch, wait_until

PDF = b"%PDF-1.4\n% E2E virtual approval document\n" + b"0" * 256


def test_attachment_upload_submit_and_download(officer, handler_password):
    password = handler_password("admin_han")  # 탐지 전에 계정이 있어야 자동 소명 요청이 간다
    platform = platform_login("admin_han")
    assert platform.get("/api/admin/members/export", params={"limit": 60}).status_code == 200

    officer_browser = argus_login(*officer)

    def probe():
        run_detection_batch()
        items = officer_browser.post("/api/detections/search", json={"size": 100}).json()["items"]
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
        f"/api/detections/{case_id}/submit",
        # 관련 업무 티켓은 소명 내용과 별도 칸 — 시드 문의 1번
        json={"content": "고객 문의 처리 중 확인 — 결재 첨부", "ticket_ids": ["INQ-1"]},
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

    # ── 관련 티켓: 담당자는 링크로 플랫폼 문의를 연다 (같은 아이디 원칙) ─────
    [ticket] = detail["explanations"][0]["tickets"]
    assert ticket == {"ticket_id": "INQ-1", "url": "http://localhost:3000/admin/inquiries/1"}


def test_officer_uses_same_id_on_platform():
    """플랫폼 백오피스 아이디 = Argus 아이디 (2026-10-02 사용자 결정)

    플랫폼 시드의 담당자 계정 officer가 동기화되어 Argus에 로그인할 수 없는 취급자 계정이 먼저
    생긴다 → README대로 create-officer officer를 실행하면 담당자 계정으로 전환된다 → 같은 아이디로
    Argus(담당자)와 플랫폼(문의 열람) 양쪽에 로그인된다.
    """
    password = secrets.token_urlsafe(18)
    wait_until(
        "플랫폼 담당자 계정 officer의 Argus 동기화",
        lambda: True if _synced("officer") else None,
    )
    result = admin_command(
        "app.scripts.users",
        "create-officer",
        "officer",
        "--password-env",
        "E2E_NEW_PASSWORD",
        "--reason",
        "E2E 플랫폼 아이디와 같은 담당자",
        env={"E2E_NEW_PASSWORD": password},
    )
    assert result.returncode == 0, result.stderr
    assert "전환" in result.stdout  # 동기화로 생긴 계정을 담당자로

    argus = argus_login("officer", password)
    assert argus.get("/api/auth/me").json()["role"] == "OFFICER"

    # 소명의 티켓 링크(/admin/inquiries/1)가 여는 화면의 데이터 — 같은 아이디의 플랫폼 계정으로
    platform = platform_login("officer")
    inquiry = platform.get("/api/admin/inquiries/1")
    assert inquiry.status_code == 200 and inquiry.json()["ticket_id"] == "INQ-1"


def _synced(login_id: str) -> bool:
    # 동기화로 생긴 A5 계정이 있으면 unlock이 "잠긴 계정이 아님"으로, 없으면 "없는 계정"으로 답한다
    result = admin_command(
        "app.scripts.users",
        "unlock",
        login_id,
        "--reason",
        "E2E 동기화 확인",  # 사유가 있어야 계정 존재 여부까지 확인한다
    )
    return "없는 계정" not in result.stderr
