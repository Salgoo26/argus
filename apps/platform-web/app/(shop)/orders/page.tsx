"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ApiError,
  api,
  customerErrorMessage,
  formatDateTime,
  type Order,
  orderAddress,
  won,
} from "@/lib/api";

export default function MyOrdersPage() {
  const router = useRouter();
  const [items, setItems] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ items: Order[] }>("/shop/orders")
      .then((body) => setItems(body.items))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setError(customerErrorMessage(e));
      });
  }, [router]);

  return (
    <>
      <h1 className="page-title">주문 내역</h1>
      <p className="page-subtitle">결제는 가상 PG(데모페이)로 흉내만 냈습니다.</p>
      {error && <div className="alert-error">{error}</div>}
      <section className="card">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th className="num">주문번호</th>
                <th>상품</th>
                <th className="num">금액</th>
                <th>결제</th>
                <th>PG 거래번호</th>
                <th>배송지</th>
                <th>주문 일시</th>
              </tr>
            </thead>
            <tbody>
              {items?.map((o) => (
                <tr key={o.id}>
                  <td className="num">{o.id}</td>
                  <td>{o.product_name}</td>
                  <td className="num">{won(o.amount)}</td>
                  <td>{o.card_company ?? "-"}</td>
                  <td className="mono">{o.pg_tid ?? "-"}</td>
                  <td>
                    {o.ship_recipient ? `${o.ship_recipient} · ${orderAddress(o)}` : "-"}
                  </td>
                  <td>{formatDateTime(o.ordered_at)}</td>
                </tr>
              ))}
              {items?.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted">
                    주문 내역이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
