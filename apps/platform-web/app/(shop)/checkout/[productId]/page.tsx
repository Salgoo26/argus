"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  customerErrorMessage,
  fullAddress,
  type Order,
  type PaymentRequest,
  type Product,
  type ShippingAddressList,
  shopLoginPath,
  won,
} from "@/lib/api";
import { PG_RESULT_PATH, PG_WINDOW, type PgResult } from "@/lib/pg";

// 주문·결제 (PLT-04·05, 기능 레이어 7-4 ③, 커머스 확장 F6)
// - 로그인 필수: 비로그인이면 로그인 화면으로 보내고, 로그인 뒤 이 상품으로 돌아온다
// - 등록한 배송지 중 하나를 고른다 (없으면 마이페이지 등록 안내). 주문에는 그 시점 값이 복사된다
// - 결제는 가상 PG(데모페이) 결제창을 **작은 창**으로 띄운다. 카드번호는 그 창(PG)에만 입력하고
//   쇼핑몰은 받지 않는다. 순서: 주문(결제대기) → PG 창에 주문·구매자 정보 POST → 카드 입력 →
//   PG가 쇼핑몰 결과 페이지로 돌려보냄 → 결과를 이 창으로 넘김 → 서버가 PG 승인 요청
// - 무통장입금(F7): 입금자명을 받아 주문(입금대기)만 만들고 주문 내역으로 — 입금 계좌·기한은 거기서 안내.
//   관리자가 입금을 확인하면 결제완료
export default function CheckoutPage() {
  const { productId } = useParams<{ productId: string }>();
  const router = useRouter();
  const [product, setProduct] = useState<Product | null>(null);
  const [addresses, setAddresses] = useState<ShippingAddressList | null>(null);
  const [addressId, setAddressId] = useState<number | null>(null);
  const [memo, setMemo] = useState("");
  const [method, setMethod] = useState<"CARD" | "BANK_TRANSFER">("CARD");
  const [depositor, setDepositor] = useState("");
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settled = useRef(false);

  useEffect(() => {
    // 배송지 조회가 곧 로그인 확인 — 401이면 로그인 화면으로(돌아올 주소를 붙여서)
    api<ShippingAddressList>("/shop/me/addresses")
      .then((body) => {
        setAddresses(body);
        setAddressId(body.items.find((a) => a.is_default)?.id ?? body.items[0]?.id ?? null);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace(shopLoginPath());
        else setError(customerErrorMessage(e));
      });
    api<{ name: string }>("/shop/me")
      .then((me) => setDepositor((d) => d || me.name))
      .catch(() => undefined);
    api<{ items: Product[] }>("/shop/products")
      .then((body) => setProduct(body.items.find((p) => String(p.id) === productId) ?? null))
      .catch((e) => setError(customerErrorMessage(e)));
  }, [productId, router]);

  if (!product || !addresses) {
    return error ? <div className="alert-error">{error}</div> : <p className="muted">불러오는 중…</p>;
  }

  async function orderByDeposit() {
    setError(null);
    setPaying(true);
    try {
      await api("/shop/orders", {
        method: "POST",
        body: JSON.stringify({
          product_id: Number(productId),
          shipping_address_id: addressId,
          delivery_memo: memo.trim() || null,
          payment_method: "BANK_TRANSFER",
          depositor_name: depositor.trim(),
        }),
      });
      router.replace("/orders");
    } catch (e) {
      setPaying(false);
      if (e instanceof ApiError && e.status === 401) router.replace(shopLoginPath());
      else setError(customerErrorMessage(e));
    }
  }

  async function pay() {
    if (method === "BANK_TRANSFER") return orderByDeposit();
    setError(null);
    // 팝업 차단을 피하려고 클릭 순간에 빈 창부터 연다 — 주문을 만든 뒤 그 창으로 결제창을 연다
    const popup = window.open("", PG_WINDOW, "width=460,height=720");
    if (!popup) {
      setError("결제창을 열 수 없습니다. 브라우저의 팝업 차단을 해제해 주세요.");
      return;
    }
    setPaying(true);
    settled.current = false;
    let created: { order: Order; payment_request: PaymentRequest };
    try {
      created = await api("/shop/orders", {
        method: "POST",
        body: JSON.stringify({
          product_id: Number(productId),
          shipping_address_id: addressId,
          delivery_memo: memo.trim() || null,
        }),
      });
    } catch (e) {
      popup.close();
      setPaying(false);
      if (e instanceof ApiError && e.status === 401) router.replace(shopLoginPath());
      else setError(customerErrorMessage(e));
      return;
    }
    const orderId = created.order.id;
    window.addEventListener("message", onMessage);
    openPg(created.payment_request, orderId);
    // 결과 없이 창을 닫으면 결제 미완료 — 주문은 30분 뒤 자동으로 만료된다
    const watch = window.setInterval(() => {
      if (popup.closed) {
        window.clearInterval(watch);
        window.setTimeout(() => {
          if (!settled.current) {
            window.removeEventListener("message", onMessage);
            setPaying(false);
            setError("결제창이 닫혀 결제가 완료되지 않았습니다.");
          }
        }, 500);
      }
    }, 500);

    async function onMessage(event: MessageEvent<PgResult>) {
      // 같은 출처(쇼핑몰 결과 페이지)에서 온, 이 주문의 결과만 받는다
      if (event.origin !== window.location.origin || event.data?.type !== "pg-result") return;
      if (event.data.orderId !== orderId || settled.current) return;
      settled.current = true;
      window.removeEventListener("message", onMessage);
      if (event.data.status === "fail") {
        setPaying(false);
        setError(`결제가 완료되지 않았습니다: ${event.data.message}`);
        return;
      }
      try {
        await api(`/shop/orders/${orderId}/payment`, {
          method: "POST",
          body: JSON.stringify({ payment_key: event.data.paymentKey, amount: event.data.amount }),
        });
        router.replace("/orders");
      } catch (e) {
        setPaying(false);
        setError(customerErrorMessage(e));
      }
    }
  }

  const items = addresses.items;

  return (
    <div className="narrow">
      <div className="card">
        <h1 className="page-title">주문하기</h1>
        {error && <div className="alert-error">{error}</div>}
        <dl className="summary">
          <dt>상품</dt>
          <dd>{product.name}</dd>
          <dt>결제 금액</dt>
          <dd>
            <strong>{won(product.price)}</strong>
          </dd>
        </dl>

        <h2 className="card-title">배송지</h2>
        {items.length === 0 ? (
          <div className="alert-info">
            등록된 배송지가 없습니다. <Link href="/mypage">마이페이지에서 배송지를 등록해 주세요.</Link>
          </div>
        ) : (
          <ul className="address-list">
            {items.map((a) => (
              <li key={a.id} className="address-item">
                <label className="check">
                  <input
                    type="radio"
                    name="shipping_address"
                    checked={addressId === a.id}
                    onChange={() => setAddressId(a.id)}
                    disabled={paying}
                  />
                  <span>
                    <strong>{a.label}</strong>{" "}
                    {a.is_default && <span className="badge badge-accent">기본</span>}
                    <br />
                    {a.recipient} · {a.phone ?? "연락처 없음"}
                    <br />
                    <span className="muted">{fullAddress(a)}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        <div className="field">
          <label htmlFor="delivery_memo">배송 메모 (선택)</label>
          <input
            id="delivery_memo"
            value={memo}
            onChange={(e) => setMemo(e.target.value)}
            maxLength={50}
            placeholder="예: 문 앞에 놓아 주세요"
            disabled={paying}
          />
          <span className="hint">택배사에 전달됩니다.</span>
        </div>

        <h2 className="card-title">결제 수단</h2>
        <div className="toolbar">
          <label className="check">
            <input
              type="radio"
              name="payment_method"
              checked={method === "CARD"}
              onChange={() => setMethod("CARD")}
              disabled={paying}
            />
            <span>카드 결제</span>
          </label>
          <label className="check">
            <input
              type="radio"
              name="payment_method"
              checked={method === "BANK_TRANSFER"}
              onChange={() => setMethod("BANK_TRANSFER")}
              disabled={paying}
            />
            <span>무통장입금</span>
          </label>
        </div>
        {method === "BANK_TRANSFER" && (
          <div className="field">
            <label htmlFor="depositor_name">입금자명</label>
            <input
              id="depositor_name"
              value={depositor}
              onChange={(e) => setDepositor(e.target.value)}
              maxLength={50}
              required
              disabled={paying}
            />
            <span className="hint">
              주문 후 안내되는 계좌로 3일 안에 입금해 주세요. 입금이 확인되면 결제완료로 바뀝니다.
            </span>
          </div>
        )}

        <button
          className="btn btn-primary"
          onClick={pay}
          disabled={
            paying || addressId === null || (method === "BANK_TRANSFER" && !depositor.trim())
          }
        >
          {method === "BANK_TRANSFER"
            ? "주문하기"
            : paying
              ? "결제창에서 결제를 진행해 주세요…"
              : "결제하기"}
        </button>
        {method === "CARD" && (
          <p className="hint">
            결제는 데모페이(가상 PG) 결제창에서 진행됩니다. 카드번호는 쇼핑몰에 전달되지 않습니다.
          </p>
        )}
      </div>
    </div>
  );
}

// PG 결제창에 주문·구매자 정보를 POST한다 — 주소창(URL)에 개인정보가 남지 않게 GET 대신 폼 전송
function openPg(request: PaymentRequest, orderId: number) {
  const back = `${window.location.origin}${PG_RESULT_PATH}?order=${orderId}`;
  const form = document.createElement("form");
  form.method = "POST";
  form.action = request.checkout_url;
  form.target = PG_WINDOW;
  const fields = { ...request.fields, successUrl: back, failUrl: `${back}&fail=1` };
  for (const [name, value] of Object.entries(fields)) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
  form.remove();
}
