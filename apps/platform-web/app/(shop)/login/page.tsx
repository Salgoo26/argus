"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { api, customerErrorMessage } from "@/lib/api";

export default function CustomerLoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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
      router.replace("/");
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
          계정이 없으면 <Link href="/signup">회원가입</Link>
        </p>
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
