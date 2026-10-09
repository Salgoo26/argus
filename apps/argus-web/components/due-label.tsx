import { formatDateTime } from "@/lib/labels";

// 소명 기한 (v0.1 보강 F-3) — 넘긴 건은 색과 "초과" 글자로. 기한을 넘겨도 상태는 그대로(담당자 판단)
export function DueLabel({ due, submitted }: { due: string; submitted?: string | null }) {
  const late = Date.parse(submitted ?? new Date().toISOString()) > Date.parse(due);
  return (
    <span className={late ? "due-over" : undefined}>
      기한 {formatDateTime(due)}
      {late && (submitted ? " (기한 뒤 제출)" : " (초과)")}
    </span>
  );
}
