"""Walking Skeleton 시나리오 E2E (CLAUDE.md 6절, actor-flows F-03 → F-04 → F-05 → F-06 → F-05)

플랫폼 관리자(ops_park) 로그인 → 회원 120건 CSV 다운로드
 → Agent → outbox → relay → Argus 수집 API → 원장(해시체인)
 → 탐지 배치: 대량 다운로드 룰 → 탐지건 + 자동 소명 요청(REQUESTED, 요청자 = 시스템)
 → 담당자 확인(마스킹) → 취급자 본인 건 확인 → 제출 → 반려 → 재요청(2차) → 재제출 → 승인
 → 해시체인 검증

각 단계에서 보안 요건도 함께 확인한다 — 원본 개인정보 미전송(CLAUDE.md 3절 #3),
마스킹·본인 건만(#8), 허용되지 않은 전이 거부(policy 3), 원장 무결성(§8③).
"""

import csv
import io
import re

from conftest import (
    admin_command,
    argus_login,
    platform_login,
    run_detection_batch,
    wait_until,
)

EXPORT_COUNT = 120
MASKED = re.compile(r"^member_\d+\*\*\*$")  # 저장 "10293" → 마스킹 member_10***


def download_members(login_id: str, count: int) -> list[dict]:
    """플랫폼 관리자 화면의 CSV 다운로드와 같은 요청 → 내려받은 행"""
    platform = platform_login(login_id)
    response = platform.get("/api/admin/members/export", params={"limit": count})
    platform.close()
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == count
    return rows


def wait_for_detection(officer_browser, actor: str, subject_count: int) -> dict:
    """relay 전송과 탐지 배치는 비동기 — 배치를 돌려 가며 해당 탐지건이 나타날 때까지 기다린다"""

    def probe():
        run_detection_batch()
        response = officer_browser.get("/api/detections", params={"size": 100})
        assert response.status_code == 200, response.text
        for item in response.json()["items"]:
            if item["actor_login_id"] == actor and item["subject_count_sum"] == subject_count:
                return item
        return None

    return wait_until(f"{actor}의 대량 다운로드 탐지건 생성", probe)


def transition(browser, detection_id: int, action: str, body: dict | None = None):
    return browser.post(f"/api/detections/{detection_id}/{action}", json=body or {})


def test_bulk_download_to_approval(officer, handler_password):
    # ── F-03 플랫폼: ops_park이 회원 120명을 CSV로 내려받는다 ────────────
    rows = download_members("ops_park", EXPORT_COUNT)
    member_ids = [row["id"] for row in rows]
    personal_values = [row[col] for row in rows for col in ("name", "email", "phone")]

    # ── F-04 수집·탐지: relay가 Argus로 보내고, 배치가 탐지 + 자동 소명 요청 ──
    officer_browser = argus_login(*officer)
    case = wait_for_detection(officer_browser, "ops_park", EXPORT_COUNT)
    detection_id = case["id"]
    assert case["status"] == "REQUESTED" and case["round"] == 1
    assert case["severity"] == "HIGH"
    assert case["distinct_subject_count"] == EXPORT_COUNT

    # ── F-05 담당자: 상세는 마스킹 상태, 원본 개인정보는 어디에도 없다 ──────
    response = officer_browser.get(f"/api/detections/{detection_id}")
    assert response.status_code == 200
    detail = response.json()
    [log] = detail["logs"]
    assert log["action"] == "DOWNLOAD" and log["subject_count"] == EXPORT_COUNT
    assert len(log["subjects"]) == EXPORT_COUNT
    assert all(MASKED.match(s) for s in log["subjects"]), log["subjects"][:3]
    body = response.text
    # 원본 식별값(회원 PK)이 응답에 실리지 않는다 — 서버에서 가려서 보낸다 (#8)
    assert not any(f"member_{i}" in body or f'"{i}"' in body for i in member_ids)
    # 이름·이메일·전화는 처음부터 Argus로 넘어오지 않는다 (#3)
    assert not any(value and value in body for value in personal_values)
    # 1차 소명 요청자는 시스템(탐지 배치)
    [first] = detail["explanations"]
    assert first["round"] == 1 and first["requested_by"] is None
    last = detail["history"][-1]
    assert (last["from_status"], last["to_status"]) == ("DETECTED", "REQUESTED")
    assert last["actor"] is None

    # ── F-06 취급자: 본인 건만 보인다 ─────────────────────────────────
    handler = argus_login("ops_park", handler_password("ops_park"))
    listed = handler.get("/api/detections").json()["items"]
    assert detection_id in [item["id"] for item in listed]
    assert handler.get(f"/api/detections/{detection_id}").status_code == 200

    other = argus_login("cs_kim", handler_password("cs_kim"))
    assert detection_id not in [item["id"] for item in other.get("/api/detections").json()["items"]]
    # 남의 건은 존재 여부도 드러나지 않게 404 (403 아님)
    assert other.get(f"/api/detections/{detection_id}").status_code == 404
    assert transition(other, detection_id, "submit", {"content": "대리 제출"}).status_code == 404

    # 취급자는 승인할 수 없다 — 역할 위반 403
    assert transition(handler, detection_id, "approve").status_code == 403

    # 1차 소명 제출
    response = transition(handler, detection_id, "submit", {"content": "월말 회원 현황 보고용"})
    assert response.json() == {"id": detection_id, "status": "SUBMITTED", "round": 1}
    # 같은 건을 두 번 제출할 수 없다 — 허용되지 않은 전이 409
    response = transition(handler, detection_id, "submit", {"content": "중복 제출"})
    assert response.status_code == 409

    # ── F-05 담당자: 반려 → 재요청(2차) ───────────────────────────────
    assert transition(officer_browser, detection_id, "reject").status_code == 400  # 사유 필수
    response = transition(
        officer_browser, detection_id, "reject", {"comment": "보고 문서 번호를 적어 주세요"}
    )
    assert response.json()["status"] == "REJECTED"
    response = transition(
        officer_browser, detection_id, "request", {"message": "문서 번호 포함해 다시 제출"}
    )
    assert response.json() == {"id": detection_id, "status": "REQUESTED", "round": 2}

    # ── F-06 취급자: 2차 제출 → F-05 담당자: 승인(종결) ─────────────────
    response = transition(
        handler, detection_id, "submit", {"content": "월말 보고(문서 OPS-2026-09) 작성용"}
    )
    assert response.json() == {"id": detection_id, "status": "SUBMITTED", "round": 2}
    response = transition(officer_browser, detection_id, "approve", {"comment": "확인"})
    assert response.json() == {"id": detection_id, "status": "APPROVED", "round": 2}
    # 종결된 건은 더 움직이지 않는다
    assert transition(officer_browser, detection_id, "reject", {"comment": "x"}).status_code == 409

    # ── 최종 상태: 차수별 소명과 상태 이력이 빠짐없이 남았다 ─────────────
    final = officer_browser.get(f"/api/detections/{detection_id}").json()
    assert final["status"] == "APPROVED" and final["closed_at"] is not None
    assert [(e["round"], e["review_result"]) for e in final["explanations"]] == [
        (1, "REJECTED"),
        (2, "APPROVED"),
    ]
    assert [e["submitted_by"] for e in final["explanations"]] == ["ops_park", "ops_park"]
    assert [(h["to_status"], h["actor"]) for h in final["history"]] == [
        ("DETECTED", None),
        ("REQUESTED", None),
        ("SUBMITTED", "ops_park"),
        ("REJECTED", officer[0]),
        ("REQUESTED", officer[0]),
        ("SUBMITTED", "ops_park"),
        ("APPROVED", officer[0]),
    ]

    for browser in (officer_browser, handler, other):
        browser.close()


def test_false_positive_request_is_cancelled(officer, handler_password):
    """시나리오의 갈림길: 담당자가 오탐으로 판단하면 자동 요청을 취소 → DISMISSED(종결)"""
    download_members("mkt_lee", 60)
    officer_browser = argus_login(*officer)
    case = wait_for_detection(officer_browser, "mkt_lee", 60)
    assert case["status"] == "REQUESTED"

    # 사유 없는 취소는 거부 — 종결 사유는 점검 근거로 남아야 한다
    assert transition(officer_browser, case["id"], "dismiss").status_code == 400
    response = transition(
        officer_browser, case["id"], "dismiss", {"reason": "사전 승인된 캠페인 대상 추출"}
    )
    assert response.json()["status"] == "DISMISSED"

    # 취소된 건에는 제출할 수 없다
    handler = argus_login("mkt_lee", handler_password("mkt_lee"))
    response = transition(handler, case["id"], "submit", {"content": "늦은 제출"})
    assert response.status_code == 409

    for browser in (officer_browser, handler):
        browser.close()


def test_ledger_hash_chain_is_intact():
    """시나리오 동안 쌓인 원장(플랫폼 + Argus 자체 접속기록) 체인이 끊기지 않았다 (§8③)"""
    result = admin_command("app.scripts.verify_chain")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK:")
