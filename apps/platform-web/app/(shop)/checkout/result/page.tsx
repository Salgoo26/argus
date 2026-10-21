"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";

import { api, customerErrorMessage } from "@/lib/api";
import type { PgResult } from "@/lib/pg";

// PG 결제창이 결제를 마치고 돌아오는 페이지 (커머스 확장 F6)
// - 성공: ?order=주문번호&paymentKey=…&orderId=ORDER-…&amount=… / 실패: ?order=…&fail=1&code=…&message=…
// - 결제창(작은 창) 안에서 열리므로 결과를 주문 화면(opener)에 넘기고 창을 닫는다.
//   승인 요청은 주문 화면이 서버에 보낸다 — 같은 출처에만 postMessage
// - 주문 화면이 없으면(새로고침·창 분리 등) 이 페이지가 직접 승인을 요청한다
function readResult(params: URLSearchParams): PgResult {
  const orderId = Number(params.get("order"));
  if (params.get("fail")) {
    return {
      type: "pg-result",
      status: "fail",
      orderId,
      code: params.get("code") ?? "PAYMENT_FAILED",
      message: params.get("message") ?? "결제가 취소되었습니다.",
    };
  }
  return {
    type: "pg-result",
    status: "success",
    orderId,
    paymentKey: params.get("paymentKey") ?? "",
    amount: Number(params.get("amount")),
  };
}

function PgResultView() {
  const params = useSearchParams();
  const router = useRouter();
  const result = useMemo(() => readResult(new URLSearchParams(params.toString())), [params]);
  const [error, setError] = useState<string | null>(null);
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const opener = window.opener as Window | null;
    if (opener && !opener.closed) {
      opener.postMessage(result, window.location.origin);
      window.close();
      return;
    }
    if (result.status === "fail") return;
    api(`/shop/orders/${result.orderId}/payment`, {
      method: "POST",
      body: JSON.stringify({ payment_key: result.paymentKey, amount: result.amount }),
    })
      .then(() => router.replace("/orders"))
      .catch((e) => setError(customerErrorMessage(e)));
  }, [result, router]);

  const failure =
    result.status === "fail" ? `결제가 완료되지 않았습니다: ${result.message}` : error;

  return (
    <div className="narrow">
      <div className="card">
        <h1 className="page-title">결제 결과</h1>
        {failure ? (
          <>
            <div className="alert-error">{failure}</div>
            <p>
              <Link href="/orders">주문 내역으로</Link>
            </p>
          </>
        ) : (
          <p className="muted">결제 결과를 확인하는 중…</p>
        )}
      </div>
    </div>
  );
}

export default function PgResultPage() {
  return (
    <Suspense fallback={<p className="muted">불러오는 중…</p>}>
      <PgResultView />
    </Suspense>
  );
}
