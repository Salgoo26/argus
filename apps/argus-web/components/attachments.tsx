"use client";

import { type ChangeEvent, useState } from "react";

import { ApiError, type Attachment, errorMessage } from "@/lib/api";

const MAX_BYTES = 5 * 1024 * 1024;
const MAX_FILES = 3;

function sizeLabel(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

async function failure(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  return new ApiError(res.status, body?.error?.code ?? "UNKNOWN", "");
}

// 내려받기 — 링크 이동이 아니라 요청으로 받아 오류(404·해시 불일치)를 화면에 보여 준다.
// 이 다운로드는 Argus 자체 접속기록으로 남는다(캡처에 개인정보가 있을 수 있음)
async function download(detectionId: number, att: Attachment): Promise<void> {
  const res = await fetch(`/api/detections/${detectionId}/attachments/${att.id}`, {
    cache: "no-store",
  });
  if (!res.ok) throw await failure(res);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = att.original_name;
  link.click();
  URL.revokeObjectURL(url);
}

export function AttachmentList({
  detectionId,
  attachments,
  onDelete,
  onError,
}: {
  detectionId: number;
  attachments: Attachment[];
  onDelete?: (att: Attachment) => void;
  onError: (message: string) => void;
}) {
  if (attachments.length === 0) return null;
  return (
    <ul className="attachments">
      {attachments.map((att) => (
        <li key={att.id}>
          <button
            className="link-button"
            onClick={() => download(detectionId, att).catch((e) => onError(errorMessage(e)))}
          >
            {att.original_name}
          </button>
          <span className="muted">
            {" "}
            {sizeLabel(att.size_bytes)} · SHA-256 {att.sha256.slice(0, 12)}…
          </span>
          {onDelete && (
            <button className="btn btn-secondary btn-small" onClick={() => onDelete(att)}>
              삭제
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

// 소명 근거자료 첨부 — 취급자, 소명 요청 중에만. PNG·JPG·PDF, 5MB, 차수당 3개 (결정 10)
// 형식·크기는 서버가 다시 검사한다(파일 내용으로 판정) — 여기 검사는 편의일 뿐
export function AttachmentUploader({
  detectionId,
  attachments,
  onChanged,
  onError,
}: {
  detectionId: number;
  attachments: Attachment[];
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function onPick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_BYTES) return onError("파일은 5MB 이하만 첨부할 수 있습니다.");
    const form = new FormData();
    form.append("file", file);
    setBusy(true);
    try {
      // Content-Type은 브라우저가 multipart 경계와 함께 정한다 — 직접 지정하지 않는다
      const res = await fetch(`/api/detections/${detectionId}/attachments`, {
        method: "POST",
        body: form,
        cache: "no-store",
      });
      if (!res.ok) throw await failure(res);
      onChanged();
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(att: Attachment) {
    try {
      const res = await fetch(`/api/detections/${detectionId}/attachments/${att.id}`, {
        method: "DELETE",
        cache: "no-store",
      });
      if (!res.ok) throw await failure(res);
      onChanged();
    } catch (e) {
      onError(errorMessage(e));
    }
  }

  return (
    <div className="uploader">
      <AttachmentList
        detectionId={detectionId}
        attachments={attachments}
        onDelete={remove}
        onError={onError}
      />
      {attachments.length < MAX_FILES && (
        <label className="btn btn-secondary">
          {busy ? "올리는 중…" : "근거자료 첨부"}
          <input
            type="file"
            accept="image/png,image/jpeg,application/pdf,.png,.jpg,.jpeg,.pdf"
            onChange={onPick}
            disabled={busy}
            hidden
          />
        </label>
      )}
      <p className="muted hint">
        결재 문서 PDF, 업무 요청 메일·티켓 캡처 등 (PNG·JPG·PDF, 파일당 5MB, 최대 {MAX_FILES}개).
        제출하면 바꿀 수 없습니다.
      </p>
    </div>
  );
}
