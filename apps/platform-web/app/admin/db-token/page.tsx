"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { ApiError, adminLoginPath, api, errorMessage, formatDateTime, type Operator } from "@/lib/api";

type IssuedToken = { token: string; token_id: string; login_id: string; expires_at: string };

// DB 접속 토큰 발급 (기능 레이어 8 — 2티어, architecture 3-4)
// DB 툴(DBeaver 등)은 DB 게이트웨이로만 접속하고, 비밀번호 칸에 이 토큰을 넣는다.
// 토큰은 서버에 저장되지 않으므로 이 화면에서 한 번만 보여준다(상태에만 두고 브라우저 저장소에 쓰지 않음).
export default function AdminDbTokenPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [issued, setIssued] = useState<IssuedToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  async function issue() {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      setIssued(await api<IssuedToken>("/admin/db-tokens", { method: "POST" }));
    } catch (e) {
      handleError(e);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!issued) return;
    await navigator.clipboard.writeText(issued.token).catch(() => undefined);
    setCopied(true);
  }

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">DB 접근 관리</h1>
        <p className="page-subtitle">DB 접속을 위한 토큰을 발급합니다. 토큰은 1시간 뒤 만료됩니다.</p>
        {error && <div className="alert-error">{error}</div>}

        <section className="card">
          <h2 className="card-title">접속 토큰 발급</h2>
          <div className="stack">
            <div className="toolbar">
              <button className="btn btn-primary" onClick={issue} disabled={busy}>
                {issued ? "새 토큰 발급" : "토큰 발급"}
              </button>
            </div>
            {issued && (
              <>
                <div className="alert-info">
                  토큰은 저장되지 않아 이 화면을 벗어나면 다시 볼 수 없습니다. 필요하면 새로 발급하세요.
                </div>
                <dl className="kv">
                  <dt>사용자 이름</dt>
                  <dd className="mono">{issued.login_id}</dd>
                  <dt>비밀번호(토큰)</dt>
                  <dd>
                    <div className="token-box mono">{issued.token}</div>
                    <button className="btn btn-secondary" onClick={copy}>
                      {copied ? "복사됨" : "복사"}
                    </button>
                  </dd>
                  <dt>만료</dt>
                  <dd>{formatDateTime(issued.expires_at)} (연장되지 않음)</dd>
                </dl>
              </>
            )}
          </div>
        </section>

        <section className="card">
          <h2 className="card-title">DB 접속 설정</h2>
          <dl className="kv">
            <dt>사용자 이름</dt>
            <dd>본인 아이디 {me && <span className="mono">({me.login_id})</span>}</dd>
            <dt>비밀번호</dt>
            <dd>발급한 토큰</dd>
            <dt>접속 주소</dt>
            <dd>
              <span className="mono">localhost:16432</span> (DB 게이트웨이)
            </dd>
          </dl>
        </section>
      </main>
    </>
  );
}
