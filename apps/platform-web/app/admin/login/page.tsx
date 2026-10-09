"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { api, errorMessage, type Operator, safeAdminPath } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      // 성공하면 서버가 HttpOnly 세션 쿠키를 심는다 — 이 코드는 토큰을 만지지 않는다
      const me = await api<Operator>("/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ login_id: form.get("login_id"), password: form.get("password") }),
      });
      // 로그인 전에 보려던 관리자 화면으로 (예: Argus 소명의 관련 티켓 링크) — 내부 경로만
      // 임시 비밀번호로 처음 로그인했으면 비밀번호부터 바꾼다 (v0.1 보강 L-2)
      if (me.must_change_password) router.replace("/admin/password");
      else router.replace(safeAdminPath(new URLSearchParams(window.location.search).get("next")));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login-page">
      <div className="card login-card">
        <div className="brand" style={{ marginBottom: 4 }}>
          <span className="brand-mark" />
          커머스 관리자
        </div>
        <p className="page-subtitle">접속 및 개인정보 처리 내역은 기록됩니다.</p>
        {error && <div className="alert-error">{error}</div>}
        <form onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="login_id">아이디</label>
            <input id="login_id" name="login_id" autoComplete="username" required maxLength={64} />
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
    </main>
  );
}
