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

// 관리자 검색 본문 (v0.1 보강 E) — 검색어는 URL이 아니라 POST 본문으로
export type SearchBody = { page: number; size: number } & Record<string, string | number>;

// 폼에서 값이 있는 칸만 골라 검색 본문으로 (번호 칸은 숫자로)
export function formCriteria(form: FormData, keys: string[], numeric: string[] = []): SearchBody {
  const body: SearchBody = { page: 1, size: 20 };
  for (const key of keys) {
    const value = String(form.get(key) ?? "").trim();
    if (value) body[key] = numeric.includes(key) ? Number(value) : value;
  }
  return body;
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
  INVALID_CREDENTIALS: "아이디(이메일) 또는 비밀번호가 올바르지 않습니다.",
  ACCOUNT_LOCKED: "로그인 5회 실패로 계정이 잠겼습니다. 관리자에게 해제를 요청하세요.",
  ACCOUNT_DISABLED: "사용할 수 없는 계정입니다.",
  UNAUTHENTICATED: "로그인이 필요합니다.",
  ACCESS_LOG_UNAVAILABLE: "접속기록을 남길 수 없어 요청을 처리하지 않았습니다. 잠시 후 다시 시도하세요.",
  BAD_REQUEST: "입력값을 확인하세요.",
  EMAIL_TAKEN: "이미 가입된 이메일입니다.",
  REQUIRED_CONSENT_MISSING: "필수 항목에 모두 동의해야 가입할 수 있습니다.",
  REQUIRED_CONSENT: "필수 동의는 철회할 수 없습니다. 원하지 않으면 회원 탈퇴를 이용하세요.",
  WRONG_PASSWORD: "비밀번호가 일치하지 않습니다.",
  NOT_FOUND: "대상을 찾을 수 없습니다.",
  ADDRESS_LIMIT: "배송지는 최대 10개까지 등록할 수 있습니다.",
  SHIPPING_ADDRESS_REQUIRED: "배송지를 선택하세요.",
  FORBIDDEN: "이 기능을 쓸 권한이 없습니다.",
  PASSWORD_CHANGE_REQUIRED: "임시 비밀번호를 먼저 바꿔야 합니다.",
  SAME_PASSWORD: "새 비밀번호가 지금 비밀번호와 같습니다.",
  SELF_CHANGE_FORBIDDEN: "본인 계정은 바꿀 수 없습니다.",
  LOGIN_ID_TAKEN: "이미 있는 아이디입니다.",
  NO_CHANGE: "바뀐 내용이 없습니다.",
  ACCOUNT_TERMINATED: "퇴직 처리된 계정입니다.",
  SHIPPING_ADDRESS_INCOMPLETE:
    "이 배송지는 연락처·우편번호가 비어 있습니다. 마이페이지에서 배송지를 보완해 주세요.",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return MESSAGES[error.code] ?? `요청에 실패했습니다 (${error.code}).`;
  return "서버에 연결할 수 없습니다.";
}

export type Operator = {
  login_id: string;
  name: string;
  team: string;
  role: string;
  // 역할별 기능 (v0.1 보강 L-1) — 메뉴를 숨기는 데만 쓴다. 허용 여부는 언제나 서버가 판단
  permissions?: Permission[];
  must_change_password?: boolean;
};

export type Permission =
  | "MEMBERS"
  | "ORDERS"
  | "INQUIRIES"
  | "MEMBER_EXPORT"
  | "REFUND_FULL_VIEW"
  | "DB_TOKEN"
  | "ACCOUNTS";

export function can(me: Operator | null, permission: Permission): boolean {
  return !!me?.permissions?.includes(permission);
}

// ── 고객 화면 ─────────────────────────────────────────

export type Consent = {
  code: string;
  name: string;
  required: boolean;
  version: string | null;
  agreed: boolean;
  acted_at: string | null;
};

export type Customer = {
  id: number;
  email: string;
  name: string;
  phone: string | null;
  created_at: string;
  consents: Consent[];
};

export type ConsentItem = {
  code: string;
  name: string;
  required: boolean;
  version: string;
  purpose: string;
  items: string;
  retention: string;
};

// 고객 로그인 잠금은 15분 뒤 자동 해제 (관리자와 다름 — 비밀번호 찾기가 없어서)
export function customerErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "ACCOUNT_LOCKED") {
    return "로그인 5회 실패로 15분 동안 로그인할 수 없습니다. 잠시 후 다시 시도하세요.";
  }
  return errorMessage(error);
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

// 서버와 같은 비밀번호 규칙 — 10자 이상, 영문·숫자·특수문자 중 2종류 이상
export function passwordProblem(value: string): string | null {
  const kinds = [/[A-Za-z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(value)).length;
  if (value.length < 10 || kinds < 2) {
    return "비밀번호는 10자 이상, 영문·숫자·특수문자 중 2종류 이상이어야 합니다.";
  }
  return null;
}

// ── 배송지 (기능 레이어 7-4 ②) ─────────────────────────────

export type ShippingAddress = {
  id: number;
  label: string;
  recipient: string;
  phone: string | null; // 예전 회원 주소에서 옮겨 온 배송지는 비어 있을 수 있다
  zip_code: string | null;
  address: string;
  address_detail: string | null;
  is_default: boolean;
};

export type ShippingAddressList = { items: ShippingAddress[]; max: number };

export function fullAddress(a: Pick<ShippingAddress, "zip_code" | "address" | "address_detail">) {
  const zip = a.zip_code ? `(${a.zip_code}) ` : "";
  return `${zip}${a.address}${a.address_detail ? ` ${a.address_detail}` : ""}`;
}

// ── 주문·결제(PG 목업)·환불계좌 ───────────────────────────

export type Product = { id: number; name: string; price: number };

// 주문의 배송 정보 — 주문할 때 고른 배송지를 복사해 둔 값 (7-4 ③). 이 기능 이전 주문·탈퇴 회원 주문은 비어 있다
export type OrderShipping = {
  ship_recipient: string | null;
  ship_phone: string | null;
  ship_zip_code: string | null;
  ship_address: string | null;
  ship_address_detail: string | null;
};

export function orderAddress(o: OrderShipping): string | null {
  if (!o.ship_address) return null;
  return fullAddress({
    zip_code: o.ship_zip_code,
    address: o.ship_address,
    address_detail: o.ship_address_detail,
  });
}

export type Order = OrderShipping & {
  id: number;
  product_name: string;
  amount: number;
  status: string;
  ordered_at: string;
  card_company: string | null;
  pg_tid: string | null;
  approved_at: string | null;
};

export type RefundAccountView = {
  bank_name: string;
  account_holder: string;
  account_last4: string;
  updated_at?: string;
} | null;

export function won(amount: number): string {
  return `${amount.toLocaleString("ko-KR")}원`;
}

// ── 1:1 문의 ─────────────────────────────────────────

export type Inquiry = {
  id: number;
  title: string;
  body: string;
  status: "OPEN" | "ANSWERED";
  answer: string | null;
  created_at: string;
  answered_at: string | null;
};

export const INQUIRY_STATUS: Record<string, string> = { OPEN: "답변 대기", ANSWERED: "답변 완료" };

// ── 고객 로그인 후 돌아갈 주소 (7-4 ③) ─────────────────────
// 비로그인으로 주문하려 하면 로그인 화면 → 로그인 뒤 원래 상품(주문 화면)으로 돌아간다.
// 관리자와 같은 원칙으로 **같은 출처의 고객 화면 경로만** 허용 — 외부 사이트·관리자 화면으로는 보내지 않는다
const SHOP_HOME = "/";

export function safeShopPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return SHOP_HOME;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return SHOP_HOME;
    if (/^\/(admin|login|signup)(\/|$)/.test(url.pathname)) return SHOP_HOME;
    return url.pathname + url.search;
  } catch {
    return SHOP_HOME;
  }
}

export function shopLoginPath(): string {
  const here = window.location.pathname + window.location.search;
  return `/login?next=${encodeURIComponent(here)}`;
}

// ── 관리자 로그인 후 돌아갈 주소 ─────────────────────────
// Argus 소명의 관련 티켓 링크로 /admin/inquiries/12에 왔는데 로그인이 안 돼 있으면,
// 로그인한 뒤 그 문의로 바로 돌아간다. 돌아갈 주소는 **같은 출처의 /admin/ 경로만** —
// next=https://evil.example 같은 값으로 외부 사이트에 튕기는 오픈 리다이렉트를 막는다.
const ADMIN_HOME = "/admin/members";

export function safeAdminPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return ADMIN_HOME;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return ADMIN_HOME;
    if (!url.pathname.startsWith("/admin/") || url.pathname.startsWith("/admin/login")) {
      return ADMIN_HOME;
    }
    return url.pathname + url.search;
  } catch {
    return ADMIN_HOME;
  }
}

export function adminLoginPath(): string {
  const here = window.location.pathname + window.location.search;
  return `/admin/login?next=${encodeURIComponent(here)}`;
}
