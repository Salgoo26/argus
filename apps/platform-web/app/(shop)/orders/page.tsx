"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  api,
  CANCEL_REASONS,
  courierStatus,
  customerErrorMessage,
  formatDate,
  formatDateTime,
  installment,
  type Order,
  ORDER_STATUS,
  type OrderStatus,
  orderAddress,
  RETURN_REASONS,
  won,
} from "@/lib/api";

const STEPS: OrderStatus[] = ["PAID", "PREPARING", "SHIPPING", "DELIVERED"];

// 주문 내역 (커머스 확장) — 배송 상태·예상 도착일·송장번호, 취소(상품준비중까지)·청약철회(배송완료 후 7일).
// 배송 상태는 가상 택배사가 알려 온 값, 결제 정보는 가상 PG 승인 결과(마스킹 카드번호)다. 결제하지 않은 주문은 없다
export default function MyOrdersPage() {
  const router = useRouter();
  const [items, setItems] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ items: Order[] }>("/shop/orders")
      .then((body) => setItems(body.items))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setError(customerErrorMessage(e));
      });
  }, [router]);

  useEffect(load, [load]);

  function replace(order: Order) {
    setItems((list) => list?.map((o) => (o.id === order.id ? order : o)) ?? null);
  }

  return (
    <>
      <h1 className="page-title">주문 내역</h1>
      {error && <div className="alert-error">{error}</div>}
      {items?.length === 0 && <div className="card muted">주문 내역이 없습니다.</div>}
      <div className="stack">
        {items?.map((o) => (
          <OrderCard key={o.id} order={o} onChanged={replace} onError={setError} />
        ))}
      </div>
    </>
  );
}

function OrderCard({
  order: o,
  onChanged,
  onError,
}: {
  order: Order;
  onChanged: (order: Order) => void;
  onError: (message: string | null) => void;
}) {
  const [claim, setClaim] = useState<"cancel" | "return" | null>(null);
  const [pending, setPending] = useState(false);
  const cancellable = o.status === "PAID" || o.status === "PREPARING";
  const returnable =
    o.status === "DELIVERED" && !!o.return_deadline && new Date(o.return_deadline) > new Date();
  const reasons = claim === "return" ? RETURN_REASONS : CANCEL_REASONS;
  const stepIndex = STEPS.indexOf(o.status);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const detail = String(form.get("reason_detail") ?? "").trim();
    setPending(true);
    onError(null);
    try {
      const updated = await api<Order>(`/shop/orders/${o.id}/${claim}`, {
        method: "POST",
        body: JSON.stringify({ reason_code: form.get("reason_code"), reason_detail: detail || null }),
      });
      onChanged(updated);
      setClaim(null);
    } catch (e) {
      onError(customerErrorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="card">
      <div className="toolbar">
        <strong>
          주문 {o.id} · {o.product_name}
        </strong>
        <span className="badge badge-accent">{ORDER_STATUS[o.status]}</span>
      </div>
      {stepIndex >= 0 && (
        <ol className="status-steps">
          {STEPS.map((s, i) => (
            <li key={s} className={i <= stepIndex ? "done" : ""}>
              {ORDER_STATUS[s]}
            </li>
          ))}
        </ol>
      )}
      <dl className="kv">
        <dt>결제</dt>
        <dd>
          {won(o.amount)} · {o.card_company ?? "-"}{" "}
          {o.card_number_masked && <span className="mono">{o.card_number_masked}</span>}
          {o.approve_no && ` · ${installment(o.installment_months)} · 승인번호 ${o.approve_no}`}
          {o.receipt_url && (
            <>
              {" · "}
              <a href={o.receipt_url} target="_blank" rel="noopener noreferrer">
                영수증
              </a>
            </>
          )}
        </dd>
        <dt>주문 일시</dt>
        <dd>{formatDateTime(o.ordered_at)}</dd>
        <dt>배송지</dt>
        <dd>{o.ship_recipient ? `${o.ship_recipient} · ${orderAddress(o)}` : "-"}</dd>
        {o.delivery_memo && (
          <>
            <dt>배송 메모</dt>
            <dd>{o.delivery_memo}</dd>
          </>
        )}
        {o.status !== "CANCELLED" && (
          <>
            <dt>{o.delivered_at ? "도착" : "예상 도착일"}</dt>
            <dd>
              {o.delivered_at ? formatDateTime(o.delivered_at) : formatDate(o.expected_delivery_at)}
            </dd>
          </>
        )}
        {(o.tracking_no || o.courier_status) && (
          <>
            <dt>택배</dt>
            <dd>
              {o.courier} <span className="mono">{o.tracking_no ?? ""}</span>
              {o.courier_status && ` · ${courierStatus(o.courier_status)}`}
            </dd>
          </>
        )}
        {o.return_deadline && o.status === "DELIVERED" && (
          <>
            <dt>청약철회 기한</dt>
            <dd>{formatDateTime(o.return_deadline)}까지</dd>
          </>
        )}
        {o.claims.map((c) => (
          <ClaimRow key={`${c.claim_type}-${c.requested_at}`} claim={c} />
        ))}
      </dl>

      {(cancellable || returnable) && claim === null && (
        <div className="toolbar">
          {cancellable && (
            <button className="btn btn-secondary" onClick={() => setClaim("cancel")}>
              주문 취소
            </button>
          )}
          {returnable && (
            <button className="btn btn-secondary" onClick={() => setClaim("return")}>
              청약철회(반품) 신청
            </button>
          )}
        </div>
      )}
      {claim && (
        <form className="claim-form" onSubmit={submit}>
          <strong>{claim === "cancel" ? "주문 취소" : "청약철회(반품) 신청"}</strong>
          <span className="hint">
            {claim === "cancel"
              ? "취소하면 카드 승인이 바로 취소됩니다. 택배사가 이미 상품을 가져간 경우에는 취소할 수 없습니다."
              : "신청하면 택배 기사가 배송지로 회수하러 갑니다. 상품이 도착해 확인되면 카드 승인이 취소됩니다."}
          </span>
          <div className="field">
            <label htmlFor={`reason-${o.id}`}>사유</label>
            <select id={`reason-${o.id}`} name="reason_code" required>
              {Object.entries(reasons).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`detail-${o.id}`}>상세 사유 (선택)</label>
            <textarea id={`detail-${o.id}`} name="reason_detail" maxLength={500} />
          </div>
          <div className="toolbar">
            <button className="btn btn-danger" type="submit" disabled={pending}>
              {pending ? "처리 중…" : claim === "cancel" ? "취소하기" : "신청하기"}
            </button>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={() => setClaim(null)}
              disabled={pending}
            >
              닫기
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function ClaimRow({ claim: c }: { claim: Order["claims"][number] }) {
  const reasons = c.claim_type === "CANCEL" ? CANCEL_REASONS : RETURN_REASONS;
  const label = c.claim_type === "CANCEL" ? "취소" : "청약철회";
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {reasons[c.reason_code] ?? c.reason_code}
        {c.reason_detail ? ` — ${c.reason_detail}` : ""} · {formatDateTime(c.requested_at)}
        {c.status === "COMPLETED" && c.refund_amount !== null
          ? ` · ${won(c.refund_amount)} 카드 승인 취소`
          : ` · ${courierStatus(c.pickup_status) ?? "처리 중"}`}
        {c.pickup_tracking_no && <span className="mono"> (회수 송장 {c.pickup_tracking_no})</span>}
      </dd>
    </>
  );
}
