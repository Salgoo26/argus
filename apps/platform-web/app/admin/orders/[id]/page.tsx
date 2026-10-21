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
  courierStatus,
  deliveryActions,
  installment,
  type Operator,
  ORDER_STATUS,
  type OrderClaim,
  type OrderDelivery,
  type OrderDeposit,
  type OrderShipping,
  type OrderStatus,
  orderAddress,
  refundLabel,
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
  OrderDelivery &
  OrderDeposit & {
    id: number;
    member_id: number | null;
    member_name: string | null;
    product_name: string;
    amount: number;
    status: OrderStatus;
    ordered_at: string;
    method: string | null;
    card_company: string | null;
    card_number_masked: string | null;
    installment_months: number | null;
    approve_no: string | null;
    receipt_url: string | null;
    pg_tid: string | null;
    approved_at: string | null;
    courier_synced_at: string | null;
    return_deadline: string | null;
    claims: (OrderClaim & { refund_pg_tid: string | null })[];
    status_history: StatusChange[];
    deposit_confirmed_by: number | null;
  };

type RefundTarget = {
  bank_name: string;
  account_holder: string;
  account_number: string;
  refund_amount: number;
};

const ACTORS: Record<StatusChange["actor_type"], string> = {
  CUSTOMER: "고객",
  OPERATOR: "관리자",
  SYSTEM: "시스템",
};

// 주문 상세 — 운영팀 배송 업무용 (기능 레이어 7-4 ③). 열면 접속기록(READ, 주문)으로 남는다.
// 배송 정보는 주문할 때 복사해 둔 값이다(회원의 현재 배송지가 아님). 카드번호는 플랫폼에 없다 —
// 가상 PG가 돌려준 마스킹 번호(앞 6·뒤 4자리)뿐. 배송중·배송완료는 가상 택배사의 통지로 바뀐다.
// 무통장입금(F7): 입금자명·금액을 보고 "입금 확인"(주문 변경 기록). 이체 환불은 고객이 고른 계좌의
// 전체 번호를 열어(결제수단 조회 기록 → Argus 결제수단 조회 룰) 이체한 뒤 "환불 완료"
export default function AdminOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [order, setOrder] = useState<AdminOrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refundTarget, setRefundTarget] = useState<RefundTarget | null>(null);

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

  // 상품 준비·택배 접수·배송 조회·반품 회수 확인 — 주문 정보 변경(접속기록 UPDATE, 주문)
  async function act(path: "advance" | "dispatch" | "sync" | "return-complete") {
    setError(null);
    try {
      await api(`/admin/deliveries/${id}/${path}`, { method: "POST" });
      load();
    } catch (e) {
      handleError(e);
    }
  }

  // 입금 확인·이체 환불 완료 — 주문 정보 변경(접속기록 UPDATE, 주문)
  async function orderAct(path: "deposit-confirm" | "refund-complete", confirmText: string) {
    if (!window.confirm(confirmText)) return;
    setError(null);
    try {
      await api(`/admin/orders/${id}/${path}`, { method: "POST" });
      setRefundTarget(null);
      load();
    } catch (e) {
      handleError(e);
    }
  }

  async function revealRefundTarget() {
    if (!window.confirm("환불할 계좌의 전체 번호를 조회합니다. 계속할까요?")) return;
    try {
      setRefundTarget(await api<RefundTarget>(`/admin/orders/${id}/refund-account`));
    } catch (e) {
      handleError(e);
    }
  }

  const actions = order ? deliveryActions(order) : [];
  const canManage = can(me, "ORDERS");
  const pickup = order?.claims.find((c) => c.claim_type === "RETURN" && c.status === "REQUESTED");
  // 택배 회수를 접수한 반품은 판매자 도착(RETURN_DELIVERED) 뒤에만 회수 확인
  const returnReady = !!pickup && (!pickup.pickup_status || pickup.pickup_status === "RETURN_DELIVERED");
  const bank = !!order?.deposit_bank_name;
  const refundPending = order?.claims.find((c) => c.refund_status === "PENDING");

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
                  <dt>배송 메모</dt>
                  <dd>{order.delivery_memo ?? "-"}</dd>
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
                <dt>택배 상태</dt>
                <dd>
                  {courierStatus(order.courier_status) ?? "택배 접수 전"}
                  {order.courier_shipment_id && (
                    <span className="muted">
                      {" "}
                      (접수번호 <span className="mono">{order.courier_shipment_id}</span>
                      {order.courier_synced_at && ` · ${formatDateTime(order.courier_synced_at)} 확인`})
                    </span>
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
              {canManage && (actions.length > 0 || order.status === "RETURN_REQUESTED") && (
                <div className="toolbar">
                  {actions.map((a) => (
                    <button
                      key={a.path}
                      className={a.path === "sync" ? "btn btn-secondary" : "btn btn-primary"}
                      onClick={() => act(a.path)}
                    >
                      {a.label}
                    </button>
                  ))}
                  {order.status === "RETURN_REQUESTED" && (
                    <button
                      className="btn btn-primary"
                      onClick={() => act("return-complete")}
                      disabled={!returnReady}
                      title={returnReady ? undefined : "택배 회수가 끝나 판매자에게 도착한 뒤 확인할 수 있습니다"}
                    >
                      {bank ? "반품 회수 확인 · 환불 대기(계좌 이체)" : "반품 회수 확인 · 환불(카드 승인 취소)"}
                    </button>
                  )}
                </div>
              )}
              {order.status === "PREPARING" && order.courier_shipment_id && (
                <p className="muted">택배사가 집하하면 배송중으로, 배송을 마치면 배송완료로 자동으로 바뀝니다.</p>
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
                        <th>택배 회수</th>
                        <th className="num">환불</th>
                        <th>환불 방식</th>
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
                            <td>
                              {courierStatus(c.pickup_status) ?? "-"}
                              {c.pickup_tracking_no && (
                                <span className="mono"> {c.pickup_tracking_no}</span>
                              )}
                            </td>
                            <td className="num">
                              {c.refund_amount !== null ? won(c.refund_amount) : "-"}
                            </td>
                            <td>
                              {refundLabel(c) ?? "-"}
                              {c.refunded_at && c.refund_method === "BANK_TRANSFER" && (
                                <span className="muted"> {formatDateTime(c.refunded_at)}</span>
                              )}
                            </td>
                            <td className="mono">{c.refund_pg_tid ?? "-"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {refundPending && can(me, "REFUND_FULL_VIEW") && (
                  <div className="stack">
                    <p className="hint">
                      무통장입금 환불 — 아래 계좌로 {won(refundPending.refund_amount ?? 0)}을 이체한 뒤
                      &quot;환불 완료&quot;를 누르세요.
                    </p>
                    {refundTarget && (
                      <div className="reveal">
                        {refundTarget.bank_name}{" "}
                        <span className="mono">{refundTarget.account_number}</span> · 예금주{" "}
                        {refundTarget.account_holder} · {won(refundTarget.refund_amount)}
                      </div>
                    )}
                    <div className="toolbar">
                      {!refundTarget && (
                        <button className="btn btn-danger" onClick={revealRefundTarget}>
                          환불 계좌 전체 보기
                        </button>
                      )}
                      <button
                        className="btn btn-primary"
                        onClick={() =>
                          orderAct("refund-complete", "고객 계좌로 이체를 마쳤나요? 환불 완료로 처리합니다.")
                        }
                      >
                        환불 완료(이체함)
                      </button>
                    </div>
                  </div>
                )}
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
              {bank ? (
                <>
                  <dl className="kv">
                    <dt>결제 수단</dt>
                    <dd>무통장입금</dd>
                    <dt>입금 계좌</dt>
                    <dd>
                      {order.deposit_bank_name}{" "}
                      <span className="mono">{order.deposit_account_number}</span> · 예금주{" "}
                      {order.deposit_account_holder}
                    </dd>
                    <dt>입금자명</dt>
                    <dd>{order.depositor_name}</dd>
                    <dt>입금 기한</dt>
                    <dd>{order.deposit_due_at ? formatDateTime(order.deposit_due_at) : "-"}</dd>
                    <dt>입금 확인</dt>
                    <dd>
                      {order.deposit_confirmed_at
                        ? `${formatDateTime(order.deposit_confirmed_at)} (관리자 #${order.deposit_confirmed_by})`
                        : "-"}
                    </dd>
                  </dl>
                  {order.status === "AWAITING_DEPOSIT" && canManage && (
                    <div className="toolbar">
                      <button
                        className="btn btn-primary"
                        onClick={() =>
                          orderAct(
                            "deposit-confirm",
                            `입금자명 "${order.depositor_name}", ${won(order.amount)} 입금을 확인했나요?`,
                          )
                        }
                      >
                        입금 확인
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <dl className="kv">
                  <dt>카드</dt>
                  <dd>
                    {order.card_company ?? "-"}{" "}
                    {order.card_number_masked && <span className="mono">{order.card_number_masked}</span>}
                  </dd>
                  <dt>할부</dt>
                  <dd>{order.approved_at ? installment(order.installment_months) : "-"}</dd>
                  <dt>승인번호</dt>
                  <dd className="mono">{order.approve_no ?? "-"}</dd>
                  <dt>PG 결제 키</dt>
                  <dd className="mono">{order.pg_tid ?? "-"}</dd>
                  <dt>승인 일시</dt>
                  <dd>{order.approved_at ? formatDateTime(order.approved_at) : "-"}</dd>
                  {order.receipt_url && (
                    <>
                      <dt>영수증</dt>
                      <dd>
                        <a href={order.receipt_url} target="_blank" rel="noopener noreferrer">
                          PG 영수증 보기
                        </a>
                      </dd>
                    </>
                  )}
                </dl>
              )}
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
