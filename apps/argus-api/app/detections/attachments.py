"""소명 근거자료 첨부 (기능 레이어 7 ③, LOG-07, 결정 10)

- POST   /api/detections/{id}/attachments                 취급자 본인, 소명 요청(REQUESTED) 중에만
- DELETE /api/detections/{id}/attachments/{attachment_id} 같은 조건 — 제출 전 잘못 올린 파일 정정
- GET    /api/detections/{id}/attachments/{attachment_id} 담당자(제출된 차수만)·취급자 본인

규칙 (결정 10)
- PNG·JPG·PDF만 — 확장자·Content-Type(보낸 쪽이 정함)이 아니라 **파일 앞부분(매직 바이트)**으로 판정
- 파일당 5MB, 소명 차수당 최대 3개
- **파일 이름을 믿지 않는다**: 저장 경로는 서버가 만든 무작위 이름(년/월/uuid) — 경로 조작(../)이
  끼어들 자리가 없다. 보낸 이름은 정리해서 표시용으로만 쓴다
- SHA-256을 올릴 때 기록하고, **내려받을 때마다 다시 계산해 다르면 내주지 않는다**(변조 탐지)
- 다운로드는 Argus 자체 접속기록(READ — 2-7절의 Argus 코드 체계를 따름) — 캡처에 개인정보가
  있을 수 있어서. 정보주체 건수는 0(파일 내용은 알 수 없음). 어느 탐지건의
  첨부인지는 context.target에 남긴다(경로 변수 값은 원장에 남기지 않음)
- 내려줄 때 브라우저가 열어 실행하지 않게: attachment + nosniff + CSP sandbox (PDF 안의 스크립트 등)
"""

import hashlib
import os
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, Response, UploadFile
from sqlalchemy import delete, func, insert, select

from app.agent import access_log, access_log_exempt, record_target
from app.auth.deps import AuthenticatedUser, CurrentUser
from app.detections.router import _not_found, _visible
from app.detections.transitions import HANDLER
from app.errors import ApiError
from app.models import detection, explanation, explanation_attachment

router = APIRouter(prefix="/api/detections")

MAX_BYTES = 5 * 1024 * 1024
MAX_PER_ROUND = 3
# 매직 바이트 → (저장할 Content-Type, 표시용 확장자)
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"%PDF-", "application/pdf", "pdf"),
)
_EXEMPT_UPLOAD = (
    "취급자 본인의 소명 자료 제출·정정 — 정보주체 조회가 아님(첨부 메타데이터·해시로 추적)"
)
_UNSAFE_NAME_CHARS = set('"<>|:*?\\/')


def detect_type(data: bytes) -> tuple[str, str] | None:
    for magic, content_type, ext in SIGNATURES:
        if data.startswith(magic):
            return content_type, ext
    return None


def display_name(raw: str | None, ext: str) -> str:
    """표시용 이름 — 경로·제어문자·따옴표를 걷어 낸다. 저장 경로에는 절대 쓰지 않는다"""
    name = unicodedata.normalize("NFC", raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in _UNSAFE_NAME_CHARS)
    name = name.strip(" .")[:200]
    return name or f"attachment.{ext}"


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.settings.attachment_dir).resolve()


def _resolve(base: Path, stored_path: str) -> Path:
    path = (base / stored_path).resolve()
    if not path.is_relative_to(base):  # DB 값이 변조돼도 볼륨 밖은 읽지 않는다
        raise ApiError(500, "ATTACHMENT_UNAVAILABLE", "attachment path is invalid")
    return path


def _editable_round(conn, user: AuthenticatedUser, detection_id: int):
    """취급자 본인 건이고 소명 요청 중일 때 그 차수의 explanation 행 (행 잠금)"""
    if user.role != HANDLER:
        raise ApiError(403, "FORBIDDEN", "only the handler can change attachments")
    case = (
        conn.execute(
            _visible(select(detection.c.id, detection.c.status, detection.c.round), user)
            .where(detection.c.id == detection_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if case is None:
        raise _not_found()
    if case["status"] != "REQUESTED":
        raise ApiError(409, "NOT_EDITABLE", "attachments can change only while requested")
    return conn.execute(
        select(explanation.c.id).where(
            explanation.c.detection_id == detection_id,
            explanation.c.round == case["round"],
        )
    ).scalar_one()


def _summary(row) -> dict:
    return {
        "id": row["id"],
        "original_name": row["original_name"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "uploaded_at": row["uploaded_at"],
    }


@router.post("/{detection_id}/attachments", status_code=201)
@access_log_exempt(_EXEMPT_UPLOAD)
def upload_attachment(
    detection_id: int, file: UploadFile, request: Request, user: CurrentUser
) -> dict:
    data = file.file.read(MAX_BYTES + 1)
    if not data:
        raise ApiError(400, "EMPTY_FILE", "file is empty")
    if len(data) > MAX_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "file must be 5MB or smaller")
    detected = detect_type(data)
    if detected is None:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "only PNG, JPG, PDF are allowed")
    content_type, ext = detected
    digest = hashlib.sha256(data).hexdigest()

    base = _base_dir(request)
    now = datetime.now(UTC)
    stored_path = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}"
    target = _resolve(base, stored_path)

    with request.app.state.engine.begin() as conn:
        explanation_id = _editable_round(conn, user, detection_id)
        count = conn.execute(
            select(func.count())
            .select_from(explanation_attachment)
            .where(explanation_attachment.c.explanation_id == explanation_id)
        ).scalar_one()
        if count >= MAX_PER_ROUND:
            raise ApiError(409, "TOO_MANY_ATTACHMENTS", "up to 3 attachments per round")

        target.parent.mkdir(parents=True, exist_ok=True)
        # 'x' — 같은 이름이 있으면 실패(덮어쓰지 않음), 0o600 — 앱 계정만 읽기
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            row = (
                conn.execute(
                    insert(explanation_attachment)
                    .values(
                        explanation_id=explanation_id,
                        original_name=display_name(file.filename, ext),
                        stored_path=stored_path,
                        content_type=content_type,
                        size_bytes=len(data),
                        sha256=digest,
                    )
                    .returning(explanation_attachment)
                )
                .mappings()
                .one()
            )
        except Exception:
            target.unlink(missing_ok=True)  # DB에 못 남긴 파일은 지운다 — 고아 파일 방지
            raise
    return _summary(row)


@router.delete("/{detection_id}/attachments/{attachment_id}", status_code=204)
@access_log_exempt(_EXEMPT_UPLOAD)
def delete_attachment(
    detection_id: int, attachment_id: int, request: Request, user: CurrentUser
) -> None:
    with request.app.state.engine.begin() as conn:
        explanation_id = _editable_round(conn, user, detection_id)
        stored_path = conn.execute(
            delete(explanation_attachment)
            .where(
                explanation_attachment.c.id == attachment_id,
                explanation_attachment.c.explanation_id == explanation_id,
            )
            .returning(explanation_attachment.c.stored_path)
        ).scalar()
        if stored_path is None:
            raise _not_found()
    # 커밋된 뒤에 파일을 지운다 — 트랜잭션이 롤백되면 파일은 남아 있어야 한다
    _resolve(_base_dir(request), stored_path).unlink(missing_ok=True)


@router.get("/{detection_id}/attachments/{attachment_id}")
@access_log(action="READ", data_category="ACCESS_LOG")
def download_attachment(
    detection_id: int, attachment_id: int, request: Request, user: CurrentUser
) -> Response:
    record_target(detection_id)
    query = (
        select(explanation_attachment, explanation.c.submitted_at)
        .join(explanation, explanation.c.id == explanation_attachment.c.explanation_id)
        .join(detection, detection.c.id == explanation.c.detection_id)
        .where(
            explanation_attachment.c.id == attachment_id,
            explanation.c.detection_id == detection_id,
        )
    )
    with request.app.state.engine.connect() as conn:
        row = conn.execute(_visible(query, user)).mappings().first()
    # 담당자는 제출된 차수의 첨부만 — 취급자가 아직 고치는 중인 초안은 보지 않는다
    if row is None or (user.role != HANDLER and row["submitted_at"] is None):
        raise _not_found()

    try:
        data = _resolve(_base_dir(request), row["stored_path"]).read_bytes()
    except OSError:
        raise ApiError(500, "ATTACHMENT_UNAVAILABLE", "attachment file is missing") from None
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        # 저장 뒤에 파일이 바뀌었다 — 근거로 쓸 수 없으니 내주지 않는다
        raise ApiError(500, "ATTACHMENT_TAMPERED", "attachment failed integrity check")

    name = row["original_name"]
    ascii_name = name.encode("ascii", "ignore").decode() or "attachment"
    return Response(
        content=data,
        media_type=row["content_type"],
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "no-store",
        },
    )
