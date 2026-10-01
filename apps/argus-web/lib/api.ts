// argus-api 화면용 API 호출 — 같은 출처의 /api/* 로 보내면 Next.js가 argus-api로 전달한다(next.config.ts).
// 세션 쿠키는 HttpOnly라 이 코드에서 읽을 수 없고, 브라우저가 알아서 실어 보낸다.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body?.error?.code ?? "UNKNOWN", body?.error?.message ?? "");
  }
  return body as T;
}

// 서버 오류 코드 → 화면 문구. 없는 ID와 틀린 비밀번호는 서버가 같은 코드로 답한다(계정 열거 방지)
const MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "아이디 또는 비밀번호가 올바르지 않습니다.",
  ACCOUNT_LOCKED: "로그인 5회 실패로 계정이 잠겼습니다. 정보보호 담당자에게 해제를 요청하세요.",
  ACCOUNT_DISABLED: "사용할 수 없는 계정입니다.",
  UNAUTHENTICATED: "로그인이 필요합니다.",
  NOT_FOUND: "탐지건을 찾을 수 없습니다.",
  FORBIDDEN: "이 작업을 할 권한이 없습니다.",
  INVALID_TRANSITION: "현재 상태에서는 할 수 없는 작업입니다. 화면을 새로고침하세요.",
  ACCESS_LOG_UNAVAILABLE: "접속기록을 남길 수 없어 요청을 처리하지 않았습니다. 잠시 후 다시 시도하세요.",
  BAD_REQUEST: "입력값을 확인하세요.",
  PERIOD_TOO_LONG: "기간은 최대 1년(366일)까지 검색할 수 있습니다.",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return MESSAGES[error.code] ?? `요청에 실패했습니다 (${error.code}).`;
  return "서버에 연결할 수 없습니다.";
}

export type Role = "OFFICER" | "HANDLER";
export type Me = { login_id: string; role: Role; name: string | null };

export type Status =
  | "DETECTED"
  | "REQUESTED"
  | "SUBMITTED"
  | "APPROVED"
  | "REJECTED"
  | "DISMISSED"
  | "ESCALATED";

export type CaseSummary = {
  id: number;
  rule_name: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  actor_login_id: string;
  actor_name: string | null;
  group_bucket: string;
  status: Status;
  round: number;
  log_count: number;
  subject_count_sum: number | null;
  distinct_subject_count: number | null;
  first_occurred_at: string;
  last_occurred_at: string;
  detected_at: string;
  closed_at: string | null;
};

export type CaseLog = {
  access_log_id: number;
  occurred_at: string;
  action: string;
  data_category: string;
  result: string;
  client_ip: string;
  request_method: string | null;
  request_path: string | null;
  request_query_keys: string[] | null;
  subject_count: number;
  subject_truncated: boolean;
  subjects: string[]; // 서버에서 이미 마스킹된 값(member_10***)만 온다
};

export type Explanation = {
  round: number;
  requested_by: string | null; // null = 시스템 자동 요청
  requested_at: string;
  request_message: string | null;
  submitted_by: string | null;
  submitted_at: string | null;
  content: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_result: "APPROVED" | "REJECTED" | null;
  review_comment: string | null;
};

export type HistoryEntry = {
  from_status: Status | null;
  to_status: Status;
  round: number;
  actor: string | null; // null = 시스템(탐지 배치)
  comment: string | null;
  created_at: string;
};

export type CaseDetail = CaseSummary & {
  rule: { name: string; description: string | null; severity: string; version: number };
  log_summary: { subject_ids_truncated?: boolean } | null;
  close_reason: string | null;
  logs: CaseLog[];
  explanations: Explanation[];
  history: HistoryEntry[];
};

// 접속기록 조회·검색 (담당자 전용)
export type Source = "PLATFORM" | "ARGUS";

export type AccessLogSearch = {
  actor?: string;
  date_from?: string; // 한국 날짜 YYYY-MM-DD, 양 끝 포함
  date_to?: string;
  action?: string;
  subject?: string; // 회원번호 — 10293 또는 member_10293
  access_path?: "APP" | "DB";
  source: Source;
  page: number;
  size: number;
};

export type AccessLogItem = {
  id: number;
  occurred_at: string;
  source: Source;
  actor_login_id: string;
  actor_name: string | null;
  client_ip: string;
  access_path: "APP" | "DB";
  action: string;
  data_category: string;
  result: "SUCCESS" | "FAILURE";
  request_method: string | null;
  request_path: string | null;
  subject_count: number;
  subject_truncated: boolean;
  subjects: string[]; // 서버에서 마스킹한 앞 몇 개만 온다
  detection_ids: number[];
};

export type AccessLogPage = {
  items: AccessLogItem[];
  page: number;
  size: number;
  total: number;
  period: { from: string; to: string };
};
