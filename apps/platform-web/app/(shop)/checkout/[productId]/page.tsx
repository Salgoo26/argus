"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api, customerErrorMessage, type Product, won } from "@/lib/api";

// 주문·결제 — PG 결제창은 흉내만 낸다 (기능 레이어 7 결정 7)
// 실제 서비스라면 결제창은 PG사 도메인에서 열리고 카드번호는 PG사만 받는다. 그래서 이 화면에는
// 카드번호 입력칸이 없다 — 쇼핑몰은 카드번호를 받지도 저장하지도 않는다(끝 4자리 포함).
export default function CheckoutPage() {
  const { productId } = useParams<{ productId: string }>();
  const router = useRouter();
  const [product, setProduct] = useState<Product | null>(null);
  const [cards, setCards] = useState<string[]>([]);
  const [card, setCard] = useState("");
  const [pgOpen, setPgOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    api<{ items: Product[]; card_companies: string[] }>("/shop/products")
      .then((body) => {
        setProduct(body.items.find((p) => String(p.id) === productId) ?? null);
        setCards(body.card_companies);
        setCard(body.card_companies[0] ?? "");
      })
      .catch((e) => setError(customerErrorMessage(e)));
  }, [productId]);

  async function approve() {
    setPending(true);
    setError(null);
    try {
      await api("/shop/orders", {
        method: "POST",
        body: JSON.stringify({ product_id: Number(productId), card_company: card }),
      });
      router.replace("/orders");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(customerErrorMessage(e));
      setPgOpen(false);
    } finally {
      setPending(false);
    }
  }

  if (!product) {
    return error ? <div className="alert-error">{error}</div> : <p className="muted">불러오는 중…</p>;
  }

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
        <button className="btn btn-primary" onClick={() => setPgOpen(true)} disabled={pgOpen}>
          결제하기
        </button>
      </div>

      {pgOpen && (
        <div className="card pg-window" role="dialog" aria-label="가상 PG 결제창">
          <div className="pg-head">데모페이 · 가상 PG 결제창</div>
          <p className="hint">
            실제 서비스라면 이 창은 PG사 화면이며 카드번호는 PG사만 받습니다. 데모 커머스는 카드번호를
            받지도 저장하지도 않고, 승인 결과(카드사·거래번호·금액)만 받습니다.
          </p>
          <div className="field">
            <label htmlFor="card">카드사 (가상)</label>
            <select id="card" value={card} onChange={(e) => setCard(e.target.value)}>
              {cards.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>
          <div className="toolbar">
            <button className="btn btn-primary" onClick={approve} disabled={pending}>
              {pending ? "승인 중…" : `${won(product.price)} 결제 승인`}
            </button>
            <button className="btn btn-secondary" onClick={() => setPgOpen(false)}>
              취소
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
