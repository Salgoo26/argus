"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import {
  ApiError,
  adminLoginPath,
  api,
  can,
  errorMessage,
  formCriteria,
  type Operator,
  type SearchBody,
} from "@/lib/api";

type Member = {
  id: number;
  name: string;
  email: string;
  phone: string | null;
  status: string;
  created_at: string;
};
type MemberPage = { items: Member[]; page: number; size: number; total: number };

const PAGE_SIZE = 20;
const FIRST_PAGE: SearchBody = { page: 1, size: PAGE_SIZE };
const SEARCH_KEYS = ["name", "email", "phone", "status", "joined_from", "joined_to"];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
}

export default function MembersPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [data, setData] = useState<MemberPage | null>(null);
  const [criteria, setCriteria] = useState<SearchBody>(FIRST_PAGE);
  const page = criteria.page;
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

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
    // 목록·검색 모두 접속기록(READ, 화면에 표시된 회원 PK)으로 남는다. 검색어는 URL이 아니라
    // 본문으로 보낸다 — 이름·이메일·연락처가 서버 로그·방문 기록에 남지 않게 (v0.1 보강 E)
    api<MemberPage>("/admin/members/search", { method: "POST", body: JSON.stringify(criteria) })
      .then(setData)
      .catch(handleError);
  }, [criteria, handleError]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCriteria(formCriteria(new FormData(event.currentTarget), SEARCH_KEYS));
  }

  async function onDownload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const params = new URLSearchParams();
    for (const key of ["limit", "joined_from", "joined_to"]) {
      const value = String(form.get(key) ?? "").trim();
      if (value) params.set(key, value);
    }
    setDownloading(true);
    setError(null);
    try {
      // 링크 이동이 아니라 요청으로 받는다 — 401·접속기록 실패(500) 등을 화면에 보여주기 위해
      const res = await fetch(`/api/admin/members/export?${params}`, { cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new ApiError(res.status, body?.error?.code ?? "UNKNOWN", "");
      }
      const name =
        res.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1] ?? "members.csv";
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      handleError(e);
    } finally {
      setDownloading(false);
    }
  }

  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">회원 관리</h1>
        {error && <div className="alert-error">{error}</div>}

        {/* 다운로드 권한이 있는 역할만 (v0.1 보강 L-1 — 운영·관리자) */}
        {can(me, "MEMBER_EXPORT") && (
        <section className="card">
          <h2 className="card-title">회원 목록 다운로드 (CSV)</h2>
          <form className="toolbar" onSubmit={onDownload}>
            <div className="field">
              <label htmlFor="limit">최대 건수</label>
              <input id="limit" name="limit" type="number" min={1} max={10000} defaultValue={120} />
            </div>
            <div className="field">
              <label htmlFor="joined_from">가입일 시작</label>
              <input id="joined_from" name="joined_from" type="date" />
            </div>
            <div className="field">
              <label htmlFor="joined_to">가입일 끝</label>
              <input id="joined_to" name="joined_to" type="date" />
            </div>
            <button className="btn btn-primary" type="submit" disabled={downloading}>
              {downloading ? "내려받는 중…" : "CSV 다운로드"}
            </button>
          </form>
        </section>
        )}

        <section className="card">
          <h2 className="card-title">회원 검색</h2>
          <form className="toolbar" onSubmit={onSearch} onReset={() => setCriteria(FIRST_PAGE)}>
            <div className="field">
              <label htmlFor="s_name">이름</label>
              <input id="s_name" name="name" maxLength={100} size={10} autoComplete="off" />
            </div>
            <div className="field">
              <label htmlFor="s_email">이메일</label>
              <input id="s_email" name="email" maxLength={100} size={16} autoComplete="off" />
            </div>
            <div className="field">
              <label htmlFor="s_phone">연락처</label>
              <input
                id="s_phone"
                name="phone"
                maxLength={20}
                size={12}
                pattern="[0-9][0-9\-]*"
                title="숫자(하이픈 있어도 됨)"
                autoComplete="off"
              />
            </div>
            <div className="field">
              <label htmlFor="s_status">상태</label>
              <select id="s_status" name="status" defaultValue="">
                <option value="">전체</option>
                <option value="ACTIVE">정상</option>
                <option value="WITHDRAWN">탈퇴</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="s_joined_from">가입일 시작</label>
              <input id="s_joined_from" name="joined_from" type="date" />
            </div>
            <div className="field">
              <label htmlFor="s_joined_to">가입일 끝</label>
              <input id="s_joined_to" name="joined_to" type="date" />
            </div>
            <button className="btn btn-primary" type="submit">
              검색
            </button>
            <button className="btn btn-secondary" type="reset">
              초기화
            </button>
          </form>
        </section>

        <section className="card">
          <h2 className="card-title">
            회원 목록 <span className="muted">총 {data?.total ?? "-"}명</span>
          </h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">회원번호</th>
                  <th>이름</th>
                  <th>이메일</th>
                  <th>연락처</th>
                  <th>상태</th>
                  <th>가입일</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((m) => (
                  <tr key={m.id}>
                    <td className="num">{m.id}</td>
                    <td>
                      <Link href={`/admin/members/${m.id}`}>{m.name}</Link>
                    </td>
                    <td>{m.email}</td>
                    <td>{m.phone ?? "-"}</td>
                    <td>
                      <span className={m.status === "ACTIVE" ? "badge badge-accent" : "badge"}>
                        {m.status === "ACTIVE" ? "정상" : "탈퇴"}
                      </span>
                    </td>
                    <td>{formatDate(m.created_at)}</td>
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
