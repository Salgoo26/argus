"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ApiError,
  api,
  customerErrorMessage,
  fullAddress,
  type Product,
  type ShippingAddressList,
  shopLoginPath,
  won,
} from "@/lib/api";

// 주문·결제 (PLT-04·05, 기능 레이어 7-4 ③)
// - 로그인 필수: 비로그인이면 로그인 화면으로 보내고, 로그인 뒤 이 상품으로 돌아온다
// - 등록한 배송지 중 하나를 고른다 (없으면 마이페이지 등록 안내). 주문에는 그 시점 값이 복사된다
// - 결제는 가상 PG 결제창. 카드번호·유효기간은 입력만 받고(검사 없음) **서버로 보내지 않는다**
//   (실제로도 카드번호는 PG사만 받는다). 서버로 가는 것은 상품·배송지·카드사뿐 — approve() 참고
export default function CheckoutPage() {
  const { productId } = useParams<{ productId: string }>();
  const router = useRouter();
  const [product, setProduct] = useState<Product | null>(null);
  const [cards, setCards] = useState<string[]>([]);
  const [addresses, setAddresses] = useState<ShippingAddressList | null>(null);
  const [addressId, setAddressId] = useState<number | null>(null);
  const [pgOpen, setPgOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    api<{ items: Product[]; card_companies: string[] }>("/shop/products")
      .then((body) => {
        setProduct(body.items.find((p) => String(p.id) === productId) ?? null);
        setCards(body.card_companies);
      })
      .catch((e) => setError(customerErrorMessage(e)));
  }, [productId, router]);

  if (!product || !addresses) {
    return error ? <div className="alert-error">{error}</div> : <p className="muted">불러오는 중…</p>;
  }

  async function approve(cardCompany: string) {
    // 카드번호는 인자로도 받지 않는다 — 결제창 컴포넌트 안에만 있고 여기로 넘어오지 않는다
    try {
      await api("/shop/orders", {
        method: "POST",
        body: JSON.stringify({
          product_id: Number(productId),
          shipping_address_id: addressId,
          card_company: cardCompany,
        }),
      });
      router.replace("/orders");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(shopLoginPath());
      else setError(customerErrorMessage(e));
      setPgOpen(false);
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
                    disabled={pgOpen}
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

        <button
          className="btn btn-primary"
          onClick={() => setPgOpen(true)}
          disabled={pgOpen || addressId === null}
        >
          결제하기
        </button>
      </div>

      {pgOpen && (
        <PgWindow
          amount={product.price}
          cards={cards}
          onApprove={approve}
          onCancel={() => setPgOpen(false)}
        />
      )}
    </div>
  );
}

// ── 가상 PG 결제창 ─────────────────────────────────────────

// 카드번호·유효기간은 결제 흐름을 보여 주기 위한 입력칸일 뿐 검사하지 않는다(2026-10-08 사용자 결정 —
// 형식 검증이 이 데모의 목적이 아님). 무엇을 입력해도 승인되고, 입력값은 어디로도 보내지 않는다
function PgWindow({
  amount,
  cards,
  onApprove,
  onCancel,
}: {
  amount: number;
  cards: string[];
  onApprove: (cardCompany: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [card, setCard] = useState(cards[0] ?? "");
  // 카드번호·유효기간은 이 컴포넌트의 상태에만 있다 — <form>·name 속성을 쓰지 않아 어떤 전송에도 실리지 않는다
  const [number, setNumber] = useState("");
  const [expiry, setExpiry] = useState("");
  const [pending, setPending] = useState(false);

  async function submit() {
    setPending(true);
    // 승인을 누르면 바로 지운다 — 승인 요청에는 카드사만 실린다
    setNumber("");
    setExpiry("");
    await onApprove(card);
    setPending(false);
  }

  return (
    <div className="card pg-window" role="dialog" aria-label="결제창">
      <div className="pg-head">데모페이 결제</div>
      <div className="pg-warning" role="alert">
        가상 결제 — 실제 카드번호 입력 금지
      </div>
      <div className="field">
        <label htmlFor="pg_card">카드사</label>
        <select id="pg_card" value={card} onChange={(e) => setCard(e.target.value)}>
          {cards.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="pg_number">카드번호</label>
        <input
          id="pg_number"
          inputMode="numeric"
          autoComplete="off"
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          maxLength={19}
        />
      </div>
      <div className="field">
        <label htmlFor="pg_expiry">유효기간 (MM/YY)</label>
        <input
          id="pg_expiry"
          inputMode="numeric"
          autoComplete="off"
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
          maxLength={5}
        />
      </div>
      <div className="toolbar">
        <button className="btn btn-primary" onClick={submit} disabled={pending}>
          {pending ? "승인 중…" : `${won(amount)} 결제 승인`}
        </button>
        <button className="btn btn-secondary" onClick={onCancel} disabled={pending}>
          취소
        </button>
      </div>
    </div>
  );
}
