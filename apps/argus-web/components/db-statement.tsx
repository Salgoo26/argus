import type { DbDetail } from "@/lib/api";

// DB 직접(2티어) 기록 한 건 — 정규화 SQL과 테이블 (policy 1-5)
// SQL은 텍스트로만 보여 준다(HTML로 해석하지 않음). 값은 $1 같은 자리표시라 개인정보가 없다
export function DbStatement({ db }: { db: DbDetail }) {
  return (
    <div className="db-statement">
      <code className="sql">{db.sql_normalized ?? "-"}</code>
      {db.tables && db.tables.length > 0 && (
        <div className="muted">테이블: {db.tables.join(", ")}</div>
      )}
    </div>
  );
}

// 정보주체 칸 — 회원번호를 특정하지 못한 DB 기록은 건수만 있다는 것을 분명히 표시
export function SubjectsCell({
  subjects,
  count,
  db,
}: {
  subjects: string[];
  count: number;
  db: DbDetail | null;
}) {
  if (db?.subject_unresolved) {
    return (
      <span className="badge badge-warn" title="결과·조건에서 회원번호를 특정하지 못한 처리 — 건수만 기록">
        미특정 {count}건
      </span>
    );
  }
  return (
    <>
      {subjects.join(", ")}
      {count > subjects.length && ` 외 ${count - subjects.length}명`}
    </>
  );
}
