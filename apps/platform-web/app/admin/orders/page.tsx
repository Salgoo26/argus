"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { ApiError, adminLoginPath, api, errorMessage, formatDateTime, type Operator, won } from "@/lib/api";

type AdminOrder = {
  id: number;
  member_id: number | null;
  member_name: string | null;
  product_name: string;
  amount: number;
  status: string;
  ordered_at: string;
  card_company: string | null;
  pg_tid: string | null;
};
type OrderPage = { items: AdminOrder[]; page: number; size: number; total: number };

const PAGE_SIZE = 20;

// 주문·결제 조회 (PLT-16) — 접속기록(READ, 주문)으로 남는다. 결제수단 정보는 카드사 이름뿐
// (카드번호는 저장하지 않음 — PG 목업)
export default function AdminOrdersPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [data, setData] = useState<OrderPage | null>(null);
  const [page, setPage] = useState(1);
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
    api<OrderPage>(`/admin/orders?page=${page}&size=${PAGE_SIZE}`).then(setData).catch(handleError);
  }, [page, handleError]);

  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">주문 조회</h1>
        <p className="page-subtitle">
          주문 조회도 접속기록으로 남습니다. 카드번호는 PG사만 처리하며 쇼핑몰은 저장하지 않습니다.
        </p>
        {error && <div className="alert-error">{error}</div>}
        <section className="card">
          <h2 className="card-title">
            주문 목록 <span className="muted">총 {data?.total ?? "-"}건</span>
          </h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">주문번호</th>
                  <th>회원</th>
                  <th>상품</th>
                  <th className="num">금액</th>
                  <th>카드사</th>
                  <th>PG 거래번호</th>
                  <th>주문 일시</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((o) => (
                  <tr key={o.id}>
                    <td className="num">
                      <Link href={`/admin/orders/${o.id}`}>{o.id}</Link>
                    </td>
                    <td>
                      {o.member_id ? (
                        <Link href={`/admin/members/${o.member_id}`}>
                          {o.member_name} ({o.member_id})
                        </Link>
                      ) : (
                        <span className="muted">탈퇴 회원</span>
                      )}
                    </td>
                    <td>{o.product_name}</td>
                    <td className="num">{won(o.amount)}</td>
                    <td>{o.card_company ?? "-"}</td>
                    <td className="mono">{o.pg_tid ?? "-"}</td>
                    <td>{formatDateTime(o.ordered_at)}</td>
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
