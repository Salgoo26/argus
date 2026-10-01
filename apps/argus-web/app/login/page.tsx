"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { api, errorMessage } from "@/lib/api";

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
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ login_id: form.get("login_id"), password: form.get("password") }),
      });
      router.replace("/detections");
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
          Argus
        </div>
        <p className="page-subtitle">
          개인정보 접속기록 점검 — 정보보호 담당자·개인정보취급자 전용. 이 화면의 로그인·조회도 접속기록으로
          남습니다.
        </p>
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
