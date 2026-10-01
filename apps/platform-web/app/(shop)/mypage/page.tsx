"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  api,
  type Customer,
  customerErrorMessage,
  formatDate,
  formatDateTime,
  passwordProblem,
} from "@/lib/api";

// 마이페이지 — 정보주체 권리 행사: 열람(§35)·정정(§36)·동의 철회(§37)·탈퇴(파기, §21)
export default function MyPage() {
  const router = useRouter();
  const [me, setMe] = useState<Customer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(customerErrorMessage(e));
    },
    [router],
  );

  useEffect(() => {
    api<Customer>("/shop/me").then(setMe).catch(handleError);
  }, [handleError]);

  function done(message: string) {
    setError(null);
    setNotice(message);
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const updated = await api<Customer>("/shop/me", {
        method: "PATCH",
        body: JSON.stringify({
          name: form.get("name"),
          phone: form.get("phone"),
          address: form.get("address"),
        }),
      });
      setMe(updated);
      done("내 정보를 저장했습니다.");
    } catch (e) {
      handleError(e);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const next = String(form.get("new_password") ?? "");
    const problem = passwordProblem(next);
    if (problem) return setError(problem);
    try {
      await api("/shop/me/password", {
        method: "POST",
        body: JSON.stringify({ current_password: form.get("current_password"), new_password: next }),
      });
      formEl.reset();
      done("비밀번호를 바꿨습니다.");
    } catch (e) {
      handleError(e);
    }
  }

  async function toggleConsent(code: string, agreed: boolean) {
    try {
      const updated = await api<Customer>(`/shop/me/consents/${code}`, {
        method: "PUT",
        body: JSON.stringify({ agreed }),
      });
      setMe(updated);
      done(agreed ? "동의했습니다." : "동의를 철회했습니다.");
    } catch (e) {
      handleError(e);
    }
  }

  async function withdraw(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (!window.confirm("탈퇴하면 회원 정보가 즉시 파기되어 되돌릴 수 없습니다. 탈퇴할까요?")) return;
    try {
      await api("/shop/me/withdraw", {
        method: "POST",
        body: JSON.stringify({ password: form.get("password") }),
      });
      router.replace("/");
    } catch (e) {
      handleError(e);
    }
  }

  if (!me) {
    return error ? <div className="alert-error">{error}</div> : <p className="muted">불러오는 중…</p>;
  }

  return (
    <>
      <h1 className="page-title">마이페이지</h1>
      <p className="page-subtitle">
        {me.email} · {formatDate(me.created_at)} 가입
      </p>
      {error && <div className="alert-error">{error}</div>}
      {notice && <div className="alert-info">{notice}</div>}

      <section className="card">
        <h2 className="card-title">내 정보</h2>
        <form className="stack" onSubmit={saveProfile}>
          <div className="field">
            <label htmlFor="name">이름</label>
            <input id="name" name="name" defaultValue={me.name} required maxLength={50} />
          </div>
          <div className="field">
            <label htmlFor="phone">휴대전화번호 (선택)</label>
            <input
              id="phone"
              name="phone"
              defaultValue={me.phone ?? ""}
              placeholder="010-0000-0000"
              maxLength={20}
            />
          </div>
          <div className="field">
            <label htmlFor="address">주소 (선택)</label>
            <input id="address" name="address" defaultValue={me.address ?? ""} maxLength={255} />
          </div>
          <div>
            <button className="btn btn-primary" type="submit">
              저장
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <h2 className="card-title">동의 내역</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>항목</th>
                <th>구분</th>
                <th>상태</th>
                <th>버전</th>
                <th>일시</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {me.consents.map((c) => (
                <tr key={c.code}>
                  <td>{c.name}</td>
                  <td>{c.required ? "필수" : "선택"}</td>
                  <td>
                    <span className={c.agreed ? "badge badge-accent" : "badge"}>
                      {c.agreed ? "동의" : "미동의"}
                    </span>
                  </td>
                  <td>{c.version ?? "-"}</td>
                  <td>{c.acted_at ? formatDateTime(c.acted_at) : "-"}</td>
                  <td>
                    {!c.required && (
                      <button
                        className="btn btn-secondary"
                        onClick={() => toggleConsent(c.code, !c.agreed)}
                      >
                        {c.agreed ? "철회" : "동의"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">필수 동의는 서비스 이용의 전제라 철회 대신 회원 탈퇴로 처리합니다.</p>
      </section>

      <section className="card">
        <h2 className="card-title">비밀번호 변경</h2>
        <form className="toolbar" onSubmit={changePassword}>
          <div className="field">
            <label htmlFor="current_password">현재 비밀번호</label>
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
              maxLength={256}
            />
          </div>
          <button className="btn btn-primary" type="submit">
            변경
          </button>
        </form>
      </section>

      <section className="card danger-zone">
        <h2 className="card-title">회원 탈퇴</h2>
        <p className="hint">
          탈퇴하면 회원 정보와 동의 내역이 <strong>즉시 파기</strong>됩니다. 관계 법령에 따라 보존해야
          하는 거래 기록은 해당 기간 동안 분리 보관한 뒤 파기합니다.
        </p>
        <form className="toolbar" onSubmit={withdraw}>
          <div className="field">
            <label htmlFor="withdraw_password">비밀번호 확인</label>
            <input
              id="withdraw_password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
            />
          </div>
          <button className="btn btn-danger" type="submit">
            탈퇴
          </button>
        </form>
      </section>
    </>
  );
}
