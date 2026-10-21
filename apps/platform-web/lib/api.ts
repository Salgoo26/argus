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
  INVALID_CREDENTIALS: "아이디 또는 비밀번호가 올바르지 않습니다.",
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
  // 커머스 확장 — 회원가입 인증·배송·취소·청약철회
  VERIFICATION_REQUIRED: "이메일 인증과 휴대폰 본인확인을 먼저 마쳐 주세요.",
  IDENTITY_CONSENT_REQUIRED: "본인확인 서비스 이용에 동의해야 인증할 수 있습니다.",
  UNDER_14: "만 14세 미만은 가입할 수 없습니다.",
  ALREADY_REGISTERED: "이미 가입한 회원입니다. 가입한 아이디로 로그인해 주세요.",
  INVALID_ORDER_STATUS: "지금 주문 상태에서는 할 수 없는 처리입니다.",
  WITHDRAWAL_PERIOD_EXPIRED: "배송완료 후 7일이 지나 청약철회(반품)를 신청할 수 없습니다.",
  ORDERS_IN_PROGRESS: "배송 중이거나 반품 처리 중인 주문이 있어 탈퇴할 수 없습니다. 처리가 끝난 뒤 다시 시도하세요.",
  SHIPPING_ADDRESS_INCOMPLETE:
    "이 배송지는 연락처·우편번호가 비어 있습니다. 마이페이지에서 배송지를 보완해 주세요.",
  // 가상 PG·택배 연동
  AMOUNT_MISMATCH: "결제 금액이 주문 금액과 다릅니다. 다시 주문해 주세요.",
  PAYMENT_FAILED: "결제 승인에 실패했습니다. 다시 시도해 주세요.",
  PAYMENT_EXPIRED: "결제 시간(30분)이 지났습니다. 다시 주문해 주세요.",
  DEPOSITOR_NAME_REQUIRED: "입금자명을 입력해 주세요.",
  DEPOSIT_EXPIRED: "입금 기한이 지난 주문입니다.",
  REFUND_ACCOUNT_REQUIRED: "환불받을 계좌를 선택해 주세요.",
  TOO_MANY_REFUND_ACCOUNTS: "환불계좌는 최대 5개까지 등록할 수 있습니다.",
  NO_PENDING_REFUND: "이체를 기다리는 환불이 없습니다.",
  REFUND_IN_PROGRESS: "처리 중인 환불이 있어 탈퇴할 수 없습니다. 환불이 끝난 뒤 다시 시도하세요.",
  PG_UNAVAILABLE: "결제 서비스(PG)에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  PG_CANCEL_FAILED: "결제 취소에 실패했습니다. 잠시 후 다시 시도해 주세요.",
  COURIER_UNAVAILABLE: "택배 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  COURIER_REJECTED: "택배 서비스가 요청을 거절했습니다.",
  ALREADY_DISPATCHED: "이미 택배 접수한 주문입니다.",
  ALREADY_SHIPPED: "택배사가 이미 상품을 가져갔습니다. 배송완료 후 반품을 신청해 주세요.",
  NOT_DISPATCHED: "택배 접수 기록이 없는 주문입니다.",
  RETURN_NOT_ARRIVED: "반품 상품이 아직 도착하지 않았습니다(택배 회수 진행 중).",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return MESSAGES[error.code] ?? `요청에 실패했습니다 (${error.code}).`;
  return "서버에 연결할 수 없습니다.";
}

// 소속 부서 (플랫폼 0015) — 고를 수 있는 값. 접근 범위는 소속이 아니라 역할로 정한다
export const TEAMS: Record<string, string> = { COMMERCE_OPS: "커머스운영팀", MARKETING: "마케팅팀", IT: "IT팀" };
// 표시용 — 권한 이력에는 개편 전 값(OPS·CS)이 그 시점 값으로 남아 있다
export const TEAM_LABELS: Record<string, string> = { ...TEAMS, OPS: "운영팀(개편 전)", CS: "CS팀(개편 전)" };

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
  login_id: string;
  email: string;
  email_verified_at: string | null;
  name: string;
  phone: string | null;
  // 휴대폰 본인확인 결과 (커머스 확장) — 예전에 가입한 회원은 비어 있다
  birth_date: string | null;
  gender: "M" | "F" | null;
  carrier: string | null;
  identity_verified_at: string | null;
  created_at: string;
  consents: Consent[];
};

export const GENDER: Record<string, string> = { M: "남", F: "여" };

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

// ── 주문·결제(가상 PG)·환불계좌 ───────────────────────────

export type Product = { id: number; name: string; price: number; ship_days?: number };

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

// 배송·취소·청약철회 (커머스 확장)
export type OrderStatus =
  | "PENDING_PAYMENT"
  | "PAYMENT_EXPIRED"
  | "AWAITING_DEPOSIT"
  | "DEPOSIT_EXPIRED"
  | "PAID"
  | "PREPARING"
  | "SHIPPING"
  | "DELIVERED"
  | "CANCELLED"
  | "RETURN_REQUESTED"
  | "RETURNED";

// 결제된 주문의 상태 — 배송 화면에 쓰는 범위 (결제대기·입금대기·미완료 제외)
export const PAID_STATUSES: OrderStatus[] = [
  "PAID",
  "PREPARING",
  "SHIPPING",
  "DELIVERED",
  "CANCELLED",
  "RETURN_REQUESTED",
  "RETURNED",
];

export const ORDER_STATUS: Record<OrderStatus, string> = {
  PENDING_PAYMENT: "결제대기",
  PAYMENT_EXPIRED: "결제 미완료",
  AWAITING_DEPOSIT: "입금대기",
  DEPOSIT_EXPIRED: "입금기한 만료",
  PAID: "결제완료",
  PREPARING: "상품준비중",
  SHIPPING: "배송중",
  DELIVERED: "배송완료",
  CANCELLED: "취소완료",
  RETURN_REQUESTED: "반품접수",
  RETURNED: "반품완료",
};

// 택배사가 알려 주는 상태 (가상 택배 서비스)
export const COURIER_STATUS: Record<string, string> = {
  RECEIVED: "택배 접수(집하 대기)",
  PICKED_UP: "집하",
  IN_TRANSIT: "간선 이동",
  OUT_FOR_DELIVERY: "배송 출발",
  DELIVERED: "배송 완료",
  CANCELED: "접수 취소",
  RETURN_RECEIVED: "회수 접수",
  RETURN_PICKED_UP: "회수 집하",
  RETURN_IN_TRANSIT: "회수 이동",
  RETURN_DELIVERED: "판매자 도착",
};

export function courierStatus(code: string | null | undefined): string | null {
  return code ? (COURIER_STATUS[code] ?? code) : null;
}

// 관리자가 할 수 있는 배송 처리 — 배송중·배송완료는 택배사 통지로 바뀌므로 관리자가 넘기지 않는다.
// 택배 접수 없이 배송중인 주문(연동 이전 데이터)만 배송완료로 넘길 수 있다
export type DeliveryAction = { label: string; path: "advance" | "dispatch" | "sync" };

export function deliveryActions(o: {
  status: OrderStatus;
  courier_shipment_id?: string | null;
  claims?: { pickup_status?: string | null }[];
}): DeliveryAction[] {
  const tracked = !!o.courier_shipment_id;
  if (o.status === "PAID") return [{ label: "상품 준비 시작", path: "advance" }];
  if (o.status === "PREPARING" && !tracked) return [{ label: "택배 접수", path: "dispatch" }];
  if (o.status === "SHIPPING" && !tracked)
    return [{ label: "배송완료로 (택배 연동 이전 주문)", path: "advance" }];
  // 반품접수는 택배 회수 상태를 다시 가져온다 (회수 접수 없는 연동 이전 반품은 서버가 안내)
  const pickup =
    o.status === "RETURN_REQUESTED" && (o.claims === undefined || o.claims.some((c) => c.pickup_status));
  if (((o.status === "PREPARING" || o.status === "SHIPPING") && tracked) || pickup)
    return [{ label: "배송 조회(택배사 상태 새로고침)", path: "sync" }];
  return [];
}

export const CANCEL_REASONS: Record<string, string> = {
  CHANGE_OF_MIND: "단순 변심",
  WRONG_ORDER: "주문 실수",
  DELAY: "배송 지연",
  OTHER: "기타",
};

export const RETURN_REASONS: Record<string, string> = {
  CHANGE_OF_MIND: "단순 변심",
  DEFECT: "상품 불량·파손",
  WRONG_ITEM: "다른 상품 배송",
  OTHER: "기타",
};

export type OrderClaim = {
  claim_type: "CANCEL" | "RETURN";
  reason_code: string;
  reason_detail: string | null;
  status: "REQUESTED" | "COMPLETED";
  requested_at: string;
  completed_at: string | null;
  refund_amount: number | null;
  refund_method?: "PG_CANCEL" | "BANK_TRANSFER" | null;
  refund_status?: "PENDING" | "DONE" | null;
  refund_bank_name?: string | null;
  refund_account_last4?: string | null;
  refunded_at?: string | null;
  pickup_status?: string | null;
  pickup_tracking_no?: string | null;
};

// 환불 처리 표시 — 카드는 PG 취소(바로 완료), 무통장입금은 관리자 이체 후 완료 (F7)
export function refundLabel(c: OrderClaim): string | null {
  if (c.refund_method === "BANK_TRANSFER") {
    const to = c.refund_bank_name ? ` (${c.refund_bank_name} ****${c.refund_account_last4})` : "";
    if (c.refund_status === "DONE") return `계좌 이체 완료${to}`;
    if (c.refund_status === "PENDING") return `계좌 이체 대기${to}`;
    return `반품 완료 후 계좌 이체${to}`;
  }
  if (c.refund_method === "PG_CANCEL") return "카드 결제 취소";
  return null;
}

export type OrderDelivery = {
  courier: string | null;
  courier_status?: string | null;
  courier_shipment_id?: string | null;
  delivery_memo?: string | null;
  tracking_no: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
  expected_delivery_at: string;
};

export type Order = OrderShipping &
  OrderDelivery & {
    id: number;
    product_name: string;
    amount: number;
    status: OrderStatus;
    ordered_at: string;
    card_company: string | null;
    card_number_masked?: string | null;
    installment_months?: number | null;
    approve_no?: string | null;
    receipt_url?: string | null;
    pg_tid: string | null;
    approved_at: string | null;
    return_deadline: string | null;
    claims: OrderClaim[];
  } & OrderDeposit;

// 무통장입금 (F7) — 주문 시점의 쇼핑몰 입금 계좌·입금자명·기한, 관리자 입금 확인
export type OrderDeposit = {
  payment_method?: "CARD" | "BANK_TRANSFER" | null;
  deposit_bank_name?: string | null;
  deposit_account_number?: string | null;
  deposit_account_holder?: string | null;
  depositor_name?: string | null;
  deposit_due_at?: string | null;
  deposit_confirmed_at?: string | null;
};

export type RefundAccount = {
  id: number;
  bank_name: string;
  account_holder: string;
  account_last4: string;
  updated_at?: string;
};

export type RefundAccountList = { items: RefundAccount[]; banks: string[]; max: number };

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

// PG 결제창에 POST할 값 (POST /shop/orders 응답) — 구매자 정보 포함, 카드번호 없음
export type PaymentRequest = {
  checkout_url: string;
  fields: Record<string, string>;
};

export function installment(months: number | null | undefined): string {
  return !months ? "일시불" : `${months}개월`;
}
