"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useState } from "react";

import { api, customerErrorMessage, safeShopPath } from "@/lib/api";

// useSearchParams는 정적 렌더링 때 Suspense 경계가 필요하다 (Next.js)
export default function CustomerLoginPage() {
  return (
    <Suspense>
      <CustomerLogin />
    </Suspense>
  );
}

function CustomerLogin() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // 로그인 뒤 돌아갈 곳 — 주문하다 왔으면 그 상품의 주문 화면 (7-4 ③). 검사는 safeShopPath가
  const next = useSearchParams().get("next");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      // 성공하면 서버가 HttpOnly 고객 세션 쿠키(customer_session)를 심는다 — 관리자 쿠키와 별개
      await api("/shop/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      router.replace(safeShopPath(next));
    } catch (e) {
      setError(customerErrorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="narrow">
      <div className="card">
        <h1 className="page-title">로그인</h1>
        <p className="page-subtitle">
          계정이 없으면{" "}
          <Link href={next ? `/signup?next=${encodeURIComponent(next)}` : "/signup"}>회원가입</Link>
        </p>
        {next?.startsWith("/checkout/") && (
          <div className="alert-info">주문하려면 로그인이 필요합니다. 로그인하면 상품 주문으로 돌아갑니다.</div>
        )}
        {error && <div className="alert-error">{error}</div>}
        <form className="stack" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="email">이메일</label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              required
              maxLength={255}
            />
          </div>
          <div className="field">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
            />
          </div>
          <button className="btn btn-primary" type="submit" disabled={pending}>
            {pending ? "로그인 중…" : "로그인"}
          </button>
        </form>
      </div>
    </div>
  );
}
