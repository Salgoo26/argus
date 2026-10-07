"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api, type Report, type ReportItem, type ReportScope } from "@/lib/api";
import { formatDateTime } from "@/lib/labels";
import { SCOPE_LABELS, lastMonth, periodLabel } from "@/lib/reports";

// 점검 보고서 (기능 레이어 9) — 담당자 전용. 만들면 Argus 접속기록에 EXPORT로 남는다
export default function ReportsPage() {
  const router = useRouter();
  const { me, handleError } = useMe();
  const [items, setItems] = useState<ReportItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const defaults = lastMonth();

  useEffect(() => {
    api<{ items: ReportItem[] }>("/reports")
      .then((page) => setItems(page.items))
      .catch((e) => setError(handleError(e)));
  }, [handleError]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const report = await api<Report>("/reports", {
        method: "POST",
        body: JSON.stringify({
          date_from: form.get("date_from"),
          date_to: form.get("date_to"),
          scope: form.get("scope"),
        }),
      });
      router.push(`/reports/${report.id}`);
    } catch (e) {
      setError(handleError(e));
      setPending(false);
    }
  }

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">점검 보고서</h1>
        <p className="page-subtitle">
          기간의 접속기록·탐지·소명 처리 결과를 경로별로 모은 점검 증적입니다(§8②). 정보주체 식별값은 마스킹되어
          실리고, 만든 순간의 내용이 그대로 보관됩니다. 보고서 생성은 Argus 접속기록에 &ldquo;보고서 출력&rdquo;으로 남습니다.
        </p>
        {error && <div className="alert-error">{error}</div>}

        <section className="card">
          <h2 className="card-title">새 보고서</h2>
          <form className="toolbar" onSubmit={create}>
            <div className="field">
              <label htmlFor="date_from">시작일</label>
              <input id="date_from" name="date_from" type="date" required defaultValue={defaults.from} />
            </div>
            <div className="field">
              <label htmlFor="date_to">종료일</label>
              <input id="date_to" name="date_to" type="date" required defaultValue={defaults.to} />
            </div>
            <div className="field">
              <label htmlFor="scope">범위</label>
              <select id="scope" name="scope" defaultValue="ALL">
                {(Object.keys(SCOPE_LABELS) as ReportScope[]).map((s) => (
                  <option key={s} value={s}>
                    {SCOPE_LABELS[s]}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn btn-primary" type="submit" disabled={pending}>
              보고서 만들기
            </button>
          </form>
        </section>

        <section className="card">
          <h2 className="card-title">지난 보고서</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">번호</th>
                  <th>기간</th>
                  <th>범위</th>
                  <th className="num">에스컬레이션</th>
                  <th>작성자</th>
                  <th>생성 시각</th>
                </tr>
              </thead>
              <tbody>
                {items?.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => router.push(`/reports/${r.id}`)}>
                    <td className="num">
                      <Link href={`/reports/${r.id}`}>#{r.id}</Link>
                    </td>
                    <td>{periodLabel(r.period_from, r.period_to)}</td>
                    <td>{SCOPE_LABELS[r.scope]}</td>
                    <td className="num">{r.escalated_count}</td>
                    <td>{r.generated_by}</td>
                    <td>{formatDateTime(r.generated_at)}</td>
                  </tr>
                ))}
                {items && items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted" style={{ textAlign: "center", padding: 24 }}>
                      아직 만든 보고서가 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </>
  );
}
