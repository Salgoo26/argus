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
  CANCEL_REASONS,
  can,
  formatDate,
  NEXT_DELIVERY_STEP,
  type Operator,
  ORDER_STATUS,
  type OrderClaim,
  type OrderDelivery,
  type OrderShipping,
  type OrderStatus,
  orderAddress,
  RETURN_REASONS,
  won,
} from "@/lib/api";

type StatusChange = {
  from_status: OrderStatus | null;
  to_status: OrderStatus;
  actor_type: "CUSTOMER" | "OPERATOR" | "SYSTEM";
  operator_id: number | null;
  changed_at: string;
};

type AdminOrderDetail = OrderShipping &
  OrderDelivery & {
    id: number;
    member_id: number | null;
    member_name: string | null;
    product_name: string;
    amount: number;
    status: OrderStatus;
    ordered_at: string;
    method: string | null;
    card_company: string | null;
    pg_tid: string | null;
    approved_at: string | null;
    return_deadline: string | null;
    claims: (OrderClaim & { refund_pg_tid: string | null })[];
    status_history: StatusChange[];
  };

const ACTORS: Record<StatusChange["actor_type"], string> = {
  CUSTOMER: "고객",
  OPERATOR: "관리자",
  SYSTEM: "시스템",
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

  const load = useCallback(() => {
    api<AdminOrderDetail>(`/admin/orders/${id}`).then(setOrder).catch(handleError);
  }, [id, handleError]);

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
    load();
  }, [load, handleError]);

  // 배송 단계 넘기기·반품 회수 확인 — 주문 정보 변경(접속기록 UPDATE, 주문)
  async function act(path: "advance" | "return-complete") {
    setError(null);
    try {
      await api(`/admin/deliveries/${id}/${path}`, { method: "POST" });
      load();
    } catch (e) {
      handleError(e);
    }
  }

  const nextStep = order ? NEXT_DELIVERY_STEP[order.status] : undefined;
  const canManage = can(me, "ORDERS");

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
                <dd>{ORDER_STATUS[order.status] ?? order.status}</dd>
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
              <h2 className="card-title">배송 진행</h2>
              <dl className="kv">
                <dt>예상 도착일</dt>
                <dd>{formatDate(order.expected_delivery_at)}</dd>
                <dt>택배</dt>
                <dd>
                  {order.tracking_no ? (
                    <>
                      {order.courier} <span className="mono">{order.tracking_no}</span>
                    </>
                  ) : (
                    "-"
                  )}
                </dd>
                <dt>발송</dt>
                <dd>{order.shipped_at ? formatDateTime(order.shipped_at) : "-"}</dd>
                <dt>도착</dt>
                <dd>{order.delivered_at ? formatDateTime(order.delivered_at) : "-"}</dd>
                {order.return_deadline && (
                  <>
                    <dt>청약철회 기한</dt>
                    <dd>{formatDateTime(order.return_deadline)}</dd>
                  </>
                )}
              </dl>
              {canManage && (nextStep || order.status === "RETURN_REQUESTED") && (
                <div className="toolbar">
                  {nextStep && (
                    <button className="btn btn-primary" onClick={() => act("advance")}>
                      {nextStep}
                    </button>
                  )}
                  {order.status === "RETURN_REQUESTED" && (
                    <button className="btn btn-primary" onClick={() => act("return-complete")}>
                      반품 회수 확인 · 환불(카드 승인 취소)
                    </button>
                  )}
                </div>
              )}
            </section>

            {order.claims.length > 0 && (
              <section className="card">
                <h2 className="card-title">취소·청약철회</h2>
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>구분</th>
                        <th>사유</th>
                        <th>신청</th>
                        <th>처리</th>
                        <th className="num">환불</th>
                        <th>취소 거래번호</th>
                      </tr>
                    </thead>
                    <tbody>
                      {order.claims.map((c) => {
                        const reasons = c.claim_type === "CANCEL" ? CANCEL_REASONS : RETURN_REASONS;
                        return (
                          <tr key={`${c.claim_type}-${c.requested_at}`}>
                            <td>{c.claim_type === "CANCEL" ? "취소" : "청약철회"}</td>
                            <td>
                              {reasons[c.reason_code] ?? c.reason_code}
                              {c.reason_detail ? ` — ${c.reason_detail}` : ""}
                            </td>
                            <td>{formatDateTime(c.requested_at)}</td>
                            <td>{c.completed_at ? formatDateTime(c.completed_at) : "처리 대기"}</td>
                            <td className="num">
                              {c.refund_amount !== null ? won(c.refund_amount) : "-"}
                            </td>
                            <td className="mono">{c.refund_pg_tid ?? "-"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <section className="card">
              <h2 className="card-title">상태 이력</h2>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>일시</th>
                      <th>변경</th>
                      <th>처리자</th>
                    </tr>
                  </thead>
                  <tbody>
                    {order.status_history.map((h, i) => (
                      <tr key={i}>
                        <td>{formatDateTime(h.changed_at)}</td>
                        <td>
                          {h.from_status ? `${ORDER_STATUS[h.from_status]} → ` : ""}
                          {ORDER_STATUS[h.to_status]}
                        </td>
                        <td>
                          {ACTORS[h.actor_type]}
                          {h.operator_id ? ` (#${h.operator_id})` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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
