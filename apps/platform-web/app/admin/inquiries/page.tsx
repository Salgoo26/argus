"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import {
  ApiError,
  adminLoginPath,
  api,
  errorMessage,
  formCriteria,
  formatDateTime,
  INQUIRY_STATUS,
  type Operator,
  type SearchBody,
} from "@/lib/api";

type AdminInquiry = {
  id: number;
  member_id: number | null;
  member_name: string | null;
  title: string;
  status: string;
  created_at: string;
  answered_at: string | null;
};
type InquiryPage = { items: AdminInquiry[]; page: number; size: number; total: number };

const PAGE_SIZE = 20;
const FIRST_PAGE: SearchBody = { page: 1, size: PAGE_SIZE };
const SEARCH_KEYS = ["member_id", "created_from", "created_to", "title"];
const TABS = [
  { key: "OPEN", label: "답변 대기" },
  { key: "ANSWERED", label: "답변 완료" },
  { key: "", label: "전체" },
];

// 1:1 문의 처리 (PLT-17, F-02) — 목록·상세·답변이 모두 접속기록으로 남고,
// 상세·답변에는 티켓 ID(INQ-번호)가 함께 기록되어 이후 고객 조회의 업무 근거가 된다
export default function AdminInquiriesPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [status, setStatus] = useState("OPEN");
  const [criteria, setCriteria] = useState<SearchBody>(FIRST_PAGE);
  const page = criteria.page;
  const [data, setData] = useState<InquiryPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace(adminLoginPath());
      else setError(errorMessage(e));
    },
    [router],
  );

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
  }, [handleError]);

  useEffect(() => {
    // 검색 조건은 본문으로 (v0.1 보강 E) — 결과로 보인 문의의 작성자가 정보주체로 남는다
    const body = status ? { ...criteria, status } : criteria;
    api<InquiryPage>("/admin/inquiries/search", { method: "POST", body: JSON.stringify(body) })
      .then(setData)
      .catch(handleError);
  }, [status, criteria, handleError]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCriteria(formCriteria(new FormData(event.currentTarget), SEARCH_KEYS, ["member_id"]));
  }

  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">1:1 문의</h1>
        <p className="page-subtitle">
          문의 조회·답변은 접속기록으로 남습니다. 문의를 연 뒤 고객 정보를 확인하면 그 문의 번호가
          조회의 업무 근거가 됩니다.
        </p>
        {error && <div className="alert-error">{error}</div>}
        <section className="card">
          <h2 className="card-title">문의 검색</h2>
          <form className="toolbar" onSubmit={onSearch} onReset={() => setCriteria(FIRST_PAGE)}>
            <div className="field">
              <label htmlFor="s_title">제목</label>
              <input id="s_title" name="title" maxLength={100} size={14} autoComplete="off" />
            </div>
            <div className="field">
              <label htmlFor="s_member_id">회원번호</label>
              <input id="s_member_id" name="member_id" type="number" min={1} style={{ width: 110 }} />
            </div>
            <div className="field">
              <label htmlFor="s_created_from">작성일 시작</label>
              <input id="s_created_from" name="created_from" type="date" />
            </div>
            <div className="field">
              <label htmlFor="s_created_to">작성일 끝</label>
              <input id="s_created_to" name="created_to" type="date" />
            </div>
            <button className="btn btn-primary" type="submit">
              검색
            </button>
            <button className="btn btn-secondary" type="reset">
              초기화
            </button>
          </form>
        </section>
        <div className="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={status === t.key ? "btn btn-primary" : "btn btn-secondary"}
              onClick={() => {
                setStatus(t.key);
                setCriteria((c) => ({ ...c, page: 1 }));
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
        <section className="card">
          <h2 className="card-title">
            문의 목록 <span className="muted">총 {data?.total ?? "-"}건</span>
          </h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>티켓</th>
                  <th>제목</th>
                  <th>작성자</th>
                  <th>상태</th>
                  <th>접수 일시</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((q) => (
                  <tr key={q.id}>
                    <td className="mono">INQ-{q.id}</td>
                    <td>
                      <Link href={`/admin/inquiries/${q.id}`}>{q.title}</Link>
                    </td>
                    <td>
                      {q.member_id ? (
                        `${q.member_name} (${q.member_id})`
                      ) : (
                        <span className="muted">탈퇴 회원</span>
                      )}
                    </td>
                    <td>
                      <span className={q.status === "ANSWERED" ? "badge badge-accent" : "badge"}>
                        {INQUIRY_STATUS[q.status]}
                      </span>
                    </td>
                    <td>{formatDateTime(q.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button
              className="btn btn-secondary"
              disabled={page <= 1}
              onClick={() => setCriteria((c) => ({ ...c, page: c.page - 1 }))}
            >
              이전
            </button>
            <span className="muted">
              {page} / {lastPage}
            </span>
            <button
              className="btn btn-secondary"
              disabled={page >= lastPage}
              onClick={() => setCriteria((c) => ({ ...c, page: c.page + 1 }))}
            >
              다음
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
