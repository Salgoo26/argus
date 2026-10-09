import type { ReportScope } from "@/lib/api";

export const SCOPE_LABELS: Record<ReportScope, string> = {
  ALL: "전체 (경로별 섹션)",
  APP: "화면 경유만",
  DB: "DB 직접만",
};

// 기본 기간 = 지난달 1일 ~ 말일 (한국 날짜) — 월 1회 이상 점검(§8②)
export function lastMonth(): { from: string; to: string } {
  const today = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const last = new Date(today.getFullYear(), today.getMonth(), 0);
  const ymd = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return { from: ymd(first), to: ymd(last) };
}

// 저장은 [시작일 00:00, 종료일 다음날 00:00) — 화면에는 양 끝 포함 날짜로
export function periodLabel(from: string, to: string): string {
  const end = new Date(new Date(to).getTime() - 1);
  const day = (d: Date) => d.toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
  return `${day(new Date(from))} ~ ${day(end)}`;
}

// 정보주체 표시 — 마스킹 값 몇 개 + "외 N명" (2026-10-07 사용자 결정)
export function subjectsLabel(subjects: string[], distinct: number, count: number): string {
  if (subjects.length === 0) return count > 0 ? `미특정 ${count}건` : "-";
  const rest = distinct - subjects.length;
  return rest > 0 ? `${subjects.join(", ")} 외 ${rest}명` : subjects.join(", ");
}
