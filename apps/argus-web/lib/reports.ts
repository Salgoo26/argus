import type { ReportScope } from "@/lib/api";

export const SCOPE_LABELS: Record<ReportScope, string> = {
  ALL: "전체 (경로별 섹션)",
  APP: "업무시스템만",
  DB: "DB 직접접근만",
};

// 기본 기간 = 이번 달 1일 ~ 오늘 (한국 날짜) — 행위 탐지 화면의 기본 기간과 같은 기준
export function thisMonth(): { from: string; to: string } {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" }); // YYYY-MM-DD
  return { from: `${today.slice(0, 8)}01`, to: today };
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
