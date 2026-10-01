import type { Role, Status } from "@/lib/api";

export const STATUS_LABELS: Record<Status, string> = {
  DETECTED: "미조치",
  REQUESTED: "소명 요청됨",
  SUBMITTED: "소명 제출됨",
  APPROVED: "승인(종결)",
  REJECTED: "반려",
  DISMISSED: "불요·취소(종결)",
  ESCALATED: "이관(종결)",
};

export function statusBadgeClass(status: Status): string {
  if (status === "REQUESTED" || status === "SUBMITTED") return "badge badge-accent";
  if (status === "DETECTED" || status === "REJECTED") return "badge badge-danger";
  return "badge";
}

export const SEVERITY_LABELS: Record<string, string> = { HIGH: "상", MEDIUM: "중", LOW: "하" };

export const ACTION_LABELS: Record<string, string> = {
  LOGIN: "로그인",
  READ: "조회",
  CREATE: "입력",
  UPDATE: "수정",
  DELETE: "삭제",
  DOWNLOAD: "다운로드",
};

export type CaseAction = "request" | "dismiss" | "submit" | "approve" | "reject" | "escalate";

export type ActionButton = {
  action: CaseAction;
  label: string;
  field: "message" | "reason" | "content" | "comment";
  required: boolean;
  style: "btn-primary" | "btn-secondary" | "btn-danger";
};

// argus-api의 상태 전이 표(app/detections/transitions.py)를 화면용으로 옮긴 것.
// 버튼을 숨기는 건 편의일 뿐이고, 허용 여부는 언제나 서버가 다시 판단한다
const OFFICER_ACTIONS: Partial<Record<Status, ActionButton[]>> = {
  DETECTED: [
    { action: "request", label: "소명 요청", field: "message", required: false, style: "btn-primary" },
    { action: "dismiss", label: "소명 불요", field: "reason", required: true, style: "btn-danger" },
  ],
  REQUESTED: [
    { action: "dismiss", label: "요청 취소(오탐)", field: "reason", required: true, style: "btn-danger" },
  ],
  SUBMITTED: [
    { action: "approve", label: "승인", field: "comment", required: false, style: "btn-primary" },
    { action: "reject", label: "반려", field: "comment", required: true, style: "btn-danger" },
  ],
  REJECTED: [
    { action: "request", label: "재요청", field: "message", required: false, style: "btn-primary" },
    { action: "escalate", label: "에스컬레이션", field: "comment", required: false, style: "btn-danger" },
  ],
};

const HANDLER_ACTIONS: Partial<Record<Status, ActionButton[]>> = {
  REQUESTED: [
    { action: "submit", label: "소명 제출", field: "content", required: true, style: "btn-primary" },
  ],
};

export function actionsFor(role: Role, status: Status): ActionButton[] {
  return (role === "OFFICER" ? OFFICER_ACTIONS : HANDLER_ACTIONS)[status] ?? [];
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false });
}
