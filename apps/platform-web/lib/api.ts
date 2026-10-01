// platform-api 호출 — 같은 출처의 /api/* 로 보내면 Next.js가 platform-api로 전달한다(next.config.ts).
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
  ACCOUNT_LOCKED: "로그인 5회 실패로 계정이 잠겼습니다. 관리자에게 해제를 요청하세요.",
  ACCOUNT_DISABLED: "사용할 수 없는 계정입니다.",
  UNAUTHENTICATED: "로그인이 필요합니다.",
  ACCESS_LOG_UNAVAILABLE: "접속기록을 남길 수 없어 요청을 처리하지 않았습니다. 잠시 후 다시 시도하세요.",
  BAD_REQUEST: "입력값을 확인하세요.",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return MESSAGES[error.code] ?? `요청에 실패했습니다 (${error.code}).`;
  return "서버에 연결할 수 없습니다.";
}

export type Operator = { login_id: string; name: string; team: string; role: string };
