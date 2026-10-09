"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import {
  ApiError,
  adminLoginPath,
  api,
  errorMessage,
  formatDateTime,
  type Operator,
  type OrderShipping,
  orderAddress,
  won,
} from "@/lib/api";

type AdminOrderDetail = OrderShipping & {
  id: number;
  member_id: number | null;
  member_name: string | null;
  product_name: string;
  amount: number;
  status: string;
  ordered_at: string;
  method: string | null;
  card_company: string | null;
  pg_tid: string | null;
  approved_at: string | null;
};

// 주문 상세 — 운영팀 배송 업무용 (기능 레이어 7-4 ③). 열면 접속기록(READ, 주문)으로 남는다.
// 배송 정보는 주문할 때 복사해 둔 값이다(회원의 현재 배송지가 아님). 카드번호는 플랫폼에 없다
export default function AdminOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [order, setOrder] = useState<AdminOrderDetail | null>(null);
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
    api<AdminOrderDetail>(`/admin/orders/${id}`).then(setOrder).catch(handleError);
  }, [id, handleError]);

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">주문 상세 {order && <span className="muted">#{order.id}</span>}</h1>
        {error && <div className="alert-error">{error}</div>}

        {order && (
          <>
            <section className="card">
              <h2 className="card-title">주문</h2>
              <dl className="kv">
                <dt>회원</dt>
                <dd>
                  {order.member_id ? (
                    <Link href={`/admin/members/${order.member_id}`}>
                      {order.member_name} ({order.member_id})
                    </Link>
                  ) : (
                    <span className="muted">탈퇴 회원</span>
                  )}
                </dd>
                <dt>상품</dt>
                <dd>{order.product_name}</dd>
                <dt>금액</dt>
                <dd>{won(order.amount)}</dd>
                <dt>상태</dt>
                <dd>{order.status === "PAID" ? "결제 완료" : order.status}</dd>
                <dt>주문 일시</dt>
                <dd>{formatDateTime(order.ordered_at)}</dd>
              </dl>
            </section>

            <section className="card">
              <h2 className="card-title">배송 정보</h2>
              {order.ship_address ? (
                <dl className="kv">
                  <dt>받는 사람</dt>
                  <dd>{order.ship_recipient}</dd>
                  <dt>연락처</dt>
                  <dd>{order.ship_phone ?? "-"}</dd>
                  <dt>주소</dt>
                  <dd>{orderAddress(order)}</dd>
                </dl>
              ) : (
                <p className="muted">
                  {order.member_id
                    ? "배송 정보가 없습니다 (배송지 기능 이전의 주문)."
                    : "탈퇴 회원의 주문 — 배송 정보는 법정 보존 기록으로 분리보관되었습니다."}
                </p>
              )}
            </section>

            <section className="card">
              <h2 className="card-title">결제</h2>
              <dl className="kv">
                <dt>카드사</dt>
                <dd>{order.card_company ?? "-"}</dd>
                <dt>PG 거래번호</dt>
                <dd className="mono">{order.pg_tid ?? "-"}</dd>
                <dt>승인 일시</dt>
                <dd>{order.approved_at ? formatDateTime(order.approved_at) : "-"}</dd>
              </dl>
            </section>
          </>
        )}
        <Link href="/admin/orders" className="btn btn-secondary">
          주문 목록
        </Link>
      </main>
    </>
  );
}
