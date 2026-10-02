"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { ApiError, api, errorMessage, type Operator } from "@/lib/api";

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

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
}

export default function MembersPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [data, setData] = useState<MemberPage | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/admin/login");
      else setError(errorMessage(e));
    },
    [router],
  );

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
  }, [handleError]);

  useEffect(() => {
    // 목록 조회도 접속기록(READ, 화면에 표시된 회원 PK)으로 남는다
    api<MemberPage>(`/admin/members?page=${page}&size=${PAGE_SIZE}`)
      .then(setData)
      .catch(handleError);
  }, [page, handleError]);

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
        <p className="page-subtitle">
          회원 개인정보 조회·다운로드는 모두 접속기록으로 남아 정보보호 담당자(Argus)가 점검합니다.
        </p>

        {error && <div className="alert-error">{error}</div>}

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
              onClick={() => setPage((p) => p - 1)}
            >
              이전
            </button>
            <span className="muted">
              {page} / {lastPage}
            </span>
            <button
              className="btn btn-secondary"
              disabled={page >= lastPage}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
