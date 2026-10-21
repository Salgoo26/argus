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
  formatDate,
  formatDateTime,
  courierStatus,
  deliveryActions,
  type Operator,
  ORDER_STATUS,
  PAID_STATUSES,
  type OrderStatus,
  type SearchBody,
} from "@/lib/api";

type Delivery = {
  id: number;
  member_id: number | null;
  product_name: string;
  status: OrderStatus;
  ordered_at: string;
  expected_delivery_at: string;
  // 목록에서는 마스킹된 값 — 전체 배송 정보는 주문 상세에서
  ship_recipient: string | null;
  ship_phone: string | null;
  ship_address: string | null;
  courier: string | null;
  courier_shipment_id: string | null;
  courier_status: string | null;
  tracking_no: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
};
type DeliveryPage = { items: Delivery[]; page: number; size: number; total: number };

const PAGE_SIZE = 20;
const FIRST_PAGE: SearchBody = { page: 1, size: PAGE_SIZE };
const SEARCH_KEYS = [
  "order_id",
  "member_id",
  "recipient",
  "tracking_no",
  "status",
  "ordered_from",
  "ordered_to",
];

// 배송·반품 관리 (커머스 확장, 판단기록 F4·F6) — 운영·CS·관리자. 목록은 받는 사람·연락처를 마스킹하고,
// 조회·검색은 접속기록(READ, 주문), 상품 준비·택배 접수·배송 조회·반품 회수 확인은 (UPDATE, 주문)으로 남는다.
// 택배 접수 뒤 배송중·배송완료는 가상 택배사의 통지로 바뀐다(관리자가 넘기지 않음)
export default function AdminDeliveriesPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [data, setData] = useState<DeliveryPage | null>(null);
  const [criteria, setCriteria] = useState<SearchBody>(FIRST_PAGE);
  const page = criteria.page;
  const [error, setError] = useState<string | null>(null);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace(adminLoginPath());
      else setError(errorMessage(e));
    },
    [router],
  );

  const load = useCallback(() => {
    api<DeliveryPage>("/admin/deliveries/search", {
      method: "POST",
      body: JSON.stringify(criteria),
    })
      .then(setData)
      .catch(handleError);
  }, [criteria, handleError]);

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
  }, [handleError]);

  useEffect(load, [load]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCriteria(
      formCriteria(new FormData(event.currentTarget), SEARCH_KEYS, ["order_id", "member_id"]),
    );
  }

  async function act(orderId: number, path: "advance" | "dispatch" | "sync" | "return-complete") {
    setError(null);
    try {
      await api(`/admin/deliveries/${orderId}/${path}`, { method: "POST" });
      load();
    } catch (e) {
      handleError(e);
    }
  }

  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">배송·반품 관리</h1>
        {error && <div className="alert-error">{error}</div>}
        <section className="card">
          <h2 className="card-title">배송 검색</h2>
          <form className="toolbar" onSubmit={onSearch} onReset={() => setCriteria(FIRST_PAGE)}>
            <div className="field">
              <label htmlFor="d_order_id">주문번호</label>
              <input id="d_order_id" name="order_id" type="number" min={1} style={{ width: 110 }} />
            </div>
            <div className="field">
              <label htmlFor="d_member_id">회원번호</label>
              <input id="d_member_id" name="member_id" type="number" min={1} style={{ width: 110 }} />
            </div>
            <div className="field">
              <label htmlFor="d_recipient">받는 사람</label>
              <input id="d_recipient" name="recipient" maxLength={100} style={{ width: 120 }} />
            </div>
            <div className="field">
              <label htmlFor="d_tracking_no">송장번호</label>
              <input id="d_tracking_no" name="tracking_no" maxLength={30} style={{ width: 150 }} />
            </div>
            <div className="field">
              <label htmlFor="d_status">상태</label>
              <select id="d_status" name="status" defaultValue="">
                <option value="">전체</option>
                {PAID_STATUSES.map((code) => (
                  <option key={code} value={code}>
                    {ORDER_STATUS[code]}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="d_ordered_from">주문일 시작</label>
              <input id="d_ordered_from" name="ordered_from" type="date" />
            </div>
            <div className="field">
              <label htmlFor="d_ordered_to">주문일 끝</label>
              <input id="d_ordered_to" name="ordered_to" type="date" />
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
            배송 목록 <span className="muted">총 {data?.total ?? "-"}건</span>
          </h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">주문번호</th>
                  <th>상품</th>
                  <th>받는 사람</th>
                  <th>연락처</th>
                  <th>주소</th>
                  <th>상태</th>
                  <th>택배 상태</th>
                  <th>예상 도착</th>
                  <th>송장번호</th>
                  <th>처리</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((d) => {
                  return (
                    <tr key={d.id}>
                      <td className="num">
                        <Link href={`/admin/orders/${d.id}`}>{d.id}</Link>
                      </td>
                      <td>{d.product_name}</td>
                      <td>{d.ship_recipient ?? "-"}</td>
                      <td>{d.ship_phone ?? "-"}</td>
                      <td>{d.ship_address ?? "-"}</td>
                      <td>{ORDER_STATUS[d.status]}</td>
                      <td>{courierStatus(d.courier_status) ?? "-"}</td>
                      <td>
                        {d.delivered_at
                          ? `${formatDateTime(d.delivered_at)} 도착`
                          : d.status === "CANCELLED"
                            ? "-"
                            : formatDate(d.expected_delivery_at)}
                      </td>
                      <td className="mono">{d.tracking_no ?? "-"}</td>
                      <td>
                        {deliveryActions(d).map((a) => (
                          <button
                            key={a.path}
                            className="btn btn-secondary"
                            onClick={() => act(d.id, a.path)}
                          >
                            {a.label}
                          </button>
                        ))}
                        {d.status === "RETURN_REQUESTED" && (
                          <button
                            className="btn btn-secondary"
                            onClick={() => act(d.id, "return-complete")}
                          >
                            회수 확인·환불
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
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
