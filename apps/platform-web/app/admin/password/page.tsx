"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { ApiError, adminLoginPath, api, errorMessage, type Operator } from "@/lib/api";

// 본인 비밀번호 변경 (v0.1 보강 L-2) — 임시 비밀번호로 처음 로그인하면 이 화면부터
export default function PasswordPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace(adminLoginPath());
      else setError(errorMessage(e));
    },
    [router],
  );

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
  }, [handleError]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get("new_password") !== form.get("confirm")) {
      setError("새 비밀번호가 서로 다릅니다.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api("/admin/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: form.get("current_password"),
          new_password: form.get("new_password"),
        }),
      });
      setDone(true);
      setMe(await api<Operator>("/admin/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.code === "BAD_REQUEST") {
        setError("새 비밀번호는 10자 이상, 영문·숫자·기호 중 두 가지 이상을 섞어 주세요.");
      } else handleError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <AppHeader me={me} />
      <main className="container narrow">
        <h1 className="page-title">비밀번호 변경</h1>
        {me?.must_change_password && (
          <div className="alert-error">임시 비밀번호를 새 비밀번호로 바꿔야 업무 화면을 쓸 수 있습니다.</div>
        )}
        {error && <div className="alert-error">{error}</div>}
        {done ? (
          <section className="card">
            <p>비밀번호를 바꿨습니다.</p>
            <button className="btn btn-primary" onClick={() => router.replace("/admin/members")}>
              계속
            </button>
          </section>
        ) : (
          <form className="card" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="current_password">지금 비밀번호</label>
              <input
                id="current_password"
                name="current_password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
              />
            </div>
            <div className="field">
              <label htmlFor="new_password">새 비밀번호</label>
              <input
                id="new_password"
                name="new_password"
                type="password"
                autoComplete="new-password"
                required
                minLength={10}
                maxLength={256}
              />
            </div>
            <div className="field">
              <label htmlFor="confirm">새 비밀번호 확인</label>
              <input
                id="confirm"
                name="confirm"
                type="password"
                autoComplete="new-password"
                required
                maxLength={256}
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={pending}>
              {pending ? "바꾸는 중…" : "바꾸기"}
            </button>
          </form>
        )}
      </main>
    </>
  );
}
