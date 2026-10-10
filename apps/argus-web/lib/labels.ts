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

export const DATA_CATEGORY_LABELS: Record<string, string> = {
  MEMBER_BASIC: "회원 기본정보",
  PAYMENT: "결제수단",
  ORDER: "주문",
  INQUIRY: "문의",
  ACCESS_LOG: "접속기록",
  NONE: "-",
};

export const SOURCE_LABELS: Record<string, string> = { PLATFORM: "플랫폼", ARGUS: "Argus" };

export const ACTION_LABELS: Record<string, string> = {
  LOGIN: "로그인",
  LOGOUT: "로그아웃",
  READ: "조회",
  CREATE: "입력",
  UPDATE: "수정",
  DELETE: "삭제",
  DOWNLOAD: "다운로드",
  EXPORT: "보고서 출력",
  UNMASK: "마스킹 해제",
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

// ── 접근 경로 (policy 1-5) — 담당자·취급자 화면 모두 같은 표기 ──
// 화면 경유(3티어)와 DB 직접(2티어)은 성격이 다른 기록이라 기록부터 소명까지 섞지 않고 구분해 보여 준다
export type AccessPath = "APP" | "DB";

export const PATH_LABELS: Record<AccessPath, string> = {
  APP: "화면 경유",
  DB: "DB 직접",
};

export function pathBadgeClass(path: AccessPath): string {
  return path === "DB" ? "badge badge-path-db" : "badge";
}

// 경로별 소명 기준 (policy 3-3) — 양식은 같고 작성 안내와 담당자 검토 기준이 다르다
export const EXPLANATION_GUIDE: Record<AccessPath, { write: string[]; review: string[] }> = {
  APP: {
    write: [
      "어떤 고객·업무를 처리하던 중이었는지",
      "관련 1:1 문의 티켓이 있으면 티켓 번호(INQ-번호)",
    ],
    review: ["업무 맥락(티켓)과 조회 대상·시점이 맞는가"],
  },
  DB: {
    write: [
      "누가 요청했는지 (요청자·부서)",
      "요청 근거 — 티켓·메일·결재 문서를 첨부",
      "작업 목적과 대상 범위 (조건·건수)",
      "데이터를 바꿨다면 변경 전후를 어떻게 확인했는지",
    ],
    review: [
      "요청 근거가 실재하는가",
      "작업 범위가 요청 범위를 넘지 않는가",
      "앱(관리자 화면)을 거치지 않고 바꿨다면 앱으로 할 수 없었던 이유가 있는가",
    ],
  },
};

// 탐지건의 행위 구분 (v0.1 보강 J-2) — 같은 날 같은 룰이라도 성격이 다르면 다른 탐지건
export const ACTION_GROUP_LABELS: Record<string, string> = {
  READ: "조회",
  DOWNLOAD: "내려받기",
  CHANGE: "변경·삭제",
  SESSION: "로그인·로그아웃",
};

// "회원 기본정보 · 조회" — 성격이 없는 건(집계 룰·이전 탐지건)은 null
export function caseNature(c: { data_category: string | null; action_group: string | null }): string | null {
  if (!c.data_category || !c.action_group) return null;
  const category = DATA_CATEGORY_LABELS[c.data_category] ?? c.data_category;
  const group = ACTION_GROUP_LABELS[c.action_group] ?? c.action_group;
  return category === "-" ? group : `${category} · ${group}`;
}
