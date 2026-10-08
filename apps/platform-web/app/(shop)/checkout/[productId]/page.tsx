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
// - 결제는 가상 PG 결제창. 카드번호·유효기간은 **이 브라우저 안에서 형식만 확인하고 서버로 보내지 않는다**
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

// Luhn 검사 — 카드번호 끝자리(검증 숫자)가 맞는지. 형식 확인일 뿐 실제 카드인지와는 무관
function luhnValid(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  }
  return sum % 10 === 0;
}

function cardProblem(rawNumber: string, rawExpiry: string): string | null {
  const digits = rawNumber.replace(/[\s-]/g, "");
  if (!/^[0-9]{15,16}$/.test(digits)) return "카드번호는 숫자 15~16자리입니다.";
  if (!luhnValid(digits)) return "카드번호가 올바르지 않습니다 (검증 숫자 불일치).";
  const m = /^(0[1-9]|1[0-2])\/?([0-9]{2})$/.exec(rawExpiry.trim());
  if (!m) return "유효기간은 MM/YY 형식입니다.";
  const now = new Date();
  const expiry = new Date(2000 + Number(m[2]), Number(m[1]), 1); // 그 달의 다음 달 1일 0시에 만료
  if (expiry <= now) return "유효기간이 지난 카드입니다.";
  return null;
}

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
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit() {
    const found = cardProblem(number, expiry);
    setProblem(found);
    if (found) return;
    setPending(true);
    // 형식 확인을 통과했으면 바로 지운다 — 승인 요청에는 카드사만 실린다
    setNumber("");
    setExpiry("");
    await onApprove(card);
    setPending(false);
  }

  return (
    <div className="card pg-window" role="dialog" aria-label="가상 PG 결제창">
      <div className="pg-head">데모페이 · 가상 PG 결제창</div>
      <div className="pg-warning" role="alert">
        가상 결제 — 실제 카드번호 입력 금지
      </div>
      <p className="hint">
        실제 서비스라면 이 창은 PG사 화면이며 카드번호는 PG사만 받습니다. 이 데모에서도 카드번호는 이
        브라우저에서 형식(자릿수·검증 숫자)만 확인하고 쇼핑몰 서버로 보내지 않습니다. 쇼핑몰은 승인
        결과(카드사·거래번호·금액)만 받습니다.
      </p>
      <div className="field">
        <label htmlFor="pg_card">카드사 (가상)</label>
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
          placeholder="1234-5678-9012-3452 (가상 번호 예시)"
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
          placeholder="12/30"
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
          maxLength={5}
        />
      </div>
      {problem && <div className="alert-error">{problem}</div>}
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
