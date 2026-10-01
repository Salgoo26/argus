"""소명 근거자료 첨부 — 형식·크기·개수 제한, 파일 이름 불신, 권한, 변조 탐지, 다운로드 기록 (③)"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.auth.passwords import hash_password
from app.detection.batch import run_batch
from app.ledger.append import append_access_logs
from app.models import (
    access_log,
    argus_user,
    detection,
    explanation_attachment,
    handler,
    source_system,
)

from conftest import TEST_CLIENT_ADDR, make_entry, reset_rules

PASSWORD = "test-password-1234"  # 테스트 전용 더미 값
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def accounts(admin_engine, password_hash, seed_rules):
    """담당자 officer, 취급자 ops_park·mkt_lee — 룰은 대량 다운로드만"""
    reset_rules(admin_engine, seed_rules, enabled=("대량 다운로드",))
    with admin_engine.begin() as conn:
        conn.execute(
            insert(argus_user).values(
                login_id="officer", password_hash=password_hash, role="OFFICER"
            )
        )
        for login_id, name in (("ops_park", "박지훈"), ("mkt_lee", "이수민")):
            handler_id = conn.execute(
                insert(handler)
                .values(
                    source_system_id=1,
                    login_id=login_id,
                    name=name,
                    team="OPS",
                    employment_status="ACTIVE",
                    last_event_at=datetime.now(UTC),
                )
                .returning(handler.c.id)
            ).scalar_one()
            conn.execute(
                insert(argus_user).values(
                    login_id=login_id,
                    password_hash=password_hash,
                    role="HANDLER",
                    handler_id=handler_id,
                )
            )
    yield
    with admin_engine.begin() as conn:
        for table in (
            "explanation_attachment",
            "detection_log",
            "detection_status_history",
            "explanation",
            "detection",
            "detection_batch_run",
            "argus_user",
            "handler",
        ):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — 고정된 테이블 이름


@pytest.fixture
def as_user(app):
    clients = []

    def login(login_id: str) -> TestClient:
        c = TestClient(app, client=TEST_CLIENT_ADDR)
        clients.append(c)
        res = c.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD})
        assert res.status_code == 200
        return c

    yield login
    for c in clients:
        c.close()


def detect(app_engine, actor="ops_park") -> int:
    """actor가 120건 다운로드 → 순찰 → 자동 소명 요청(REQUESTED)된 탐지건 id"""
    with app_engine.begin() as conn:
        append_access_logs(
            conn,
            [
                make_entry(
                    actor_login_id=actor,
                    action="DOWNLOAD",
                    subject_ids=[str(n) for n in range(10001, 10121)],
                    subject_count=120,
                    request_path="/admin/members/export",
                )
            ],
            received_at=datetime.now(UTC),
        )
    run_batch(app_engine)
    with app_engine.connect() as conn:
        return conn.execute(
            select(detection.c.id)
            .where(detection.c.actor_login_id == actor)
            .order_by(detection.c.id.desc())
        ).scalar()


def upload(client, case: int, data: bytes = PNG, name: str = "결재문서.png"):
    return client.post(
        f"/api/detections/{case}/attachments",
        files={"file": (name, data, "application/octet-stream")},
    )


def attachment_dir(app) -> Path:
    return Path(app.state.settings.attachment_dir)


# ── 올리기 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(PNG, "image/png"), (JPG, "image/jpeg"), (PDF, "application/pdf")],
)
def test_handler_uploads_png_jpg_pdf(app, app_engine, as_user, data, content_type):
    case = detect(app_engine)
    res = upload(as_user("ops_park"), case, data)

    assert res.status_code == 201
    body = res.json()
    assert body["content_type"] == content_type and body["size_bytes"] == len(data)
    assert len(body["sha256"]) == 64


@pytest.mark.parametrize(
    "data",
    [b"<script>alert(1)</script>", b"MZ\x90\x00 executable", b"GIF89a" + b"\x00" * 10],
    ids=["html", "exe", "gif"],
)
def test_other_types_are_rejected_by_content_not_name(app_engine, as_user, data):
    case = detect(app_engine)
    # 이름이 .png여도 내용이 PNG가 아니면 거부 — 확장자·Content-Type을 믿지 않는다
    res = upload(as_user("ops_park"), case, data, name="evidence.png")
    assert res.status_code == 415 and res.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_file_over_5mb_is_rejected(app_engine, as_user):
    case = detect(app_engine)
    res = upload(as_user("ops_park"), case, PNG + b"\x00" * (5 * 1024 * 1024))
    assert res.status_code == 413


def test_empty_file_is_rejected(app_engine, as_user):
    case = detect(app_engine)
    assert upload(as_user("ops_park"), case, b"").status_code == 400


def test_at_most_three_per_round(app_engine, as_user):
    case = detect(app_engine)
    handler_client = as_user("ops_park")
    for _ in range(3):
        assert upload(handler_client, case).status_code == 201
    res = upload(handler_client, case)
    assert res.status_code == 409 and res.json()["error"]["code"] == "TOO_MANY_ATTACHMENTS"


def test_filename_is_not_trusted_for_storage(app, app_engine, admin_engine, as_user):
    case = detect(app_engine)
    res = upload(as_user("ops_park"), case, name="../../../etc/passwd<x>.png")

    assert res.status_code == 201
    assert res.json()["original_name"] == "passwdx.png"  # 경로·꺾쇠 제거, 표시용만
    with admin_engine.connect() as conn:
        stored = conn.execute(select(explanation_attachment.c.stored_path)).scalar_one()
    assert ".." not in stored and "passwd" not in stored  # 저장 이름은 서버가 만든 uuid
    path = attachment_dir(app) / stored
    assert path.read_bytes() == PNG


def test_only_owner_handler_can_upload(app_engine, as_user):
    case = detect(app_engine)
    assert upload(as_user("officer"), case).status_code == 403
    assert upload(as_user("mkt_lee"), case).status_code == 404  # 남의 건은 존재도 숨김


def test_upload_only_while_requested(app_engine, as_user):
    case = detect(app_engine)
    handler_client = as_user("ops_park")
    handler_client.post(f"/api/detections/{case}/submit", json={"content": "업무상 필요"})
    res = upload(handler_client, case)
    assert res.status_code == 409 and res.json()["error"]["code"] == "NOT_EDITABLE"


def test_delete_before_submit_removes_file(app, app_engine, admin_engine, as_user):
    case = detect(app_engine)
    handler_client = as_user("ops_park")
    att = upload(handler_client, case).json()
    with admin_engine.connect() as conn:
        stored = conn.execute(select(explanation_attachment.c.stored_path)).scalar_one()
    path = attachment_dir(app) / stored
    assert path.exists()

    assert (
        handler_client.delete(f"/api/detections/{case}/attachments/{att['id']}").status_code == 204
    )
    assert not path.exists()
    assert (
        handler_client.delete(f"/api/detections/{case}/attachments/{att['id']}").status_code == 404
    )


def test_uploads_are_not_self_access_logged(app_engine, as_user):
    case = detect(app_engine)
    upload(as_user("ops_park"), case)
    with app_engine.connect() as conn:
        paths = conn.execute(
            select(access_log.c.request_path)
            .join(source_system, source_system.c.id == access_log.c.source_system_id)
            .where(source_system.c.code == "ARGUS")
        ).scalars()
        assert not any("attachments" in p for p in paths)


# ── 보기·내려받기 ─────────────────────────────────────────


def _submitted_case(app_engine, as_user) -> tuple[int, int]:
    case = detect(app_engine)
    handler_client = as_user("ops_park")
    att = upload(handler_client, case, PDF, "결재.pdf").json()
    handler_client.post(f"/api/detections/{case}/submit", json={"content": "결재 문서 첨부"})
    return case, att["id"]


def test_detail_lists_attachments_by_round(app_engine, as_user):
    case, att_id = _submitted_case(app_engine, as_user)
    [round1] = as_user("officer").get(f"/api/detections/{case}").json()["explanations"]
    [att] = round1["attachments"]
    assert att["id"] == att_id and att["original_name"] == "결재.pdf"
    assert "stored_path" not in att


def test_officer_does_not_see_draft_attachments(app_engine, as_user):
    case = detect(app_engine)
    att = upload(as_user("ops_park"), case).json()
    officer = as_user("officer")

    [round1] = officer.get(f"/api/detections/{case}").json()["explanations"]
    assert round1["attachments"] == []
    assert officer.get(f"/api/detections/{case}/attachments/{att['id']}").status_code == 404


def test_download_is_safe_and_self_logged(app_engine, as_user):
    case, att_id = _submitted_case(app_engine, as_user)
    res = as_user("officer").get(f"/api/detections/{case}/attachments/{att_id}")

    assert res.status_code == 200 and res.content == PDF
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''%EA%B2%B0%EC%9E%AC.pdf" in res.headers["content-disposition"]
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-security-policy"] == "sandbox"

    with app_engine.connect() as conn:
        [log] = conn.execute(
            select(access_log)
            .join(source_system, source_system.c.id == access_log.c.source_system_id)
            .where(source_system.c.code == "ARGUS", access_log.c.request_path.like("%attachments%"))
        ).mappings()
    assert log["actor_login_id"] == "officer" and log["data_category"] == "ACCESS_LOG"
    assert log["action"] == "READ" and log["subject_count"] == 0
    assert log["request_path"] == "/api/detections/{detection_id}/attachments/{attachment_id}"
    assert log["context"] == {"target": {"detection_id": case}}


def test_others_cannot_download(app_engine, as_user):
    case, att_id = _submitted_case(app_engine, as_user)
    assert as_user("mkt_lee").get(f"/api/detections/{case}/attachments/{att_id}").status_code == 404
    other_case = detect(app_engine, actor="mkt_lee")
    # 다른 탐지건 번호로 남의 첨부를 꺼내지 못한다
    url = f"/api/detections/{other_case}/attachments/{att_id}"
    assert as_user("mkt_lee").get(url).status_code == 404


def test_tampered_file_is_not_served(app, app_engine, admin_engine, as_user):
    case, att_id = _submitted_case(app_engine, as_user)
    with admin_engine.connect() as conn:
        stored = conn.execute(select(explanation_attachment.c.stored_path)).scalar_one()
    (attachment_dir(app) / stored).write_bytes(PDF + b"tampered")

    res = as_user("officer").get(f"/api/detections/{case}/attachments/{att_id}")
    assert res.status_code == 500 and res.json()["error"]["code"] == "ATTACHMENT_TAMPERED"


def test_app_account_cannot_update_attachment_rows(app_engine):
    # 해시·경로를 바꿔 근거를 갈아치우지 못하게 — 앱 계정엔 UPDATE 권한이 없다
    with pytest.raises(Exception, match="permission denied"), app_engine.begin() as conn:
        conn.execute(text("UPDATE explanation_attachment SET sha256 = repeat('0', 64)"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"..\..\windows\evil.pdf", "evil.pdf"),
        ("a\x00b\x07c.png", "abc.png"),  # 제어문자
        ("   ", "attachment.png"),
        (None, "attachment.png"),
        ("가" * 300 + ".png", "가" * 200),
    ],
)
def test_display_name_sanitizing(raw, expected):
    from app.detections.attachments import display_name

    assert display_name(raw, "png") == expected
