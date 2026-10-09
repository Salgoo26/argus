"use client";

import { type FormEvent, useEffect, useState } from "react";

import { api, customerErrorMessage, type RefundAccountView } from "@/lib/api";

type Body = { refund_account: RefundAccountView; banks: string[] };

// 환불계좌 — 서버는 계좌번호를 AES-256-GCM으로 암호화해 저장하고, 화면에는 끝 4자리만 돌려준다
export function RefundAccountCard() {
  const [data, setData] = useState<Body | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Body>("/shop/me/refund-account")
      .then(setData)
      .catch((e) => setError(customerErrorMessage(e)));
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const body = await api<Body>("/shop/me/refund-account", {
        method: "PUT",
        body: JSON.stringify({
          bank_name: form.get("bank_name"),
          account_holder: form.get("account_holder"),
          account_number: form.get("account_number"),
        }),
      });
      setData(body);
      setEditing(false);
      setError(null);
    } catch (e) {
      setError(customerErrorMessage(e));
    }
  }

  async function remove() {
    if (!window.confirm("환불계좌를 삭제할까요?")) return;
    try {
      await api("/shop/me/refund-account", { method: "DELETE" });
      setData((d) => (d ? { ...d, refund_account: null } : d));
    } catch (e) {
      setError(customerErrorMessage(e));
    }
  }

  const account = data?.refund_account ?? null;

  return (
    <section className="card">
      <h2 className="card-title">환불계좌 (선택)</h2>
      {error && <div className="alert-error">{error}</div>}
      {account && !editing && (
        <div className="toolbar">
          <span>
            {account.bank_name} ****{account.account_last4} · 예금주 {account.account_holder}
          </span>
          <button className="btn btn-secondary" onClick={() => setEditing(true)}>
            변경
          </button>
          <button className="btn btn-danger" onClick={remove}>
            삭제
          </button>
        </div>
      )}
      {!account && !editing && (
        <button className="btn btn-secondary" onClick={() => setEditing(true)}>
          환불계좌 등록
        </button>
      )}
      {editing && data && (
        <form className="toolbar" onSubmit={save}>
          <div className="field">
            <label htmlFor="bank_name">은행</label>
            <select id="bank_name" name="bank_name" defaultValue={account?.bank_name}>
              {data.banks.map((b) => (
                <option key={b}>{b}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="account_holder">예금주</label>
            <input id="account_holder" name="account_holder" required maxLength={50} />
          </div>
          <div className="field">
            <label htmlFor="account_number">계좌번호 (숫자 10~16자리)</label>
            <input
              id="account_number"
              name="account_number"
              inputMode="numeric"
              autoComplete="off"
              required
              maxLength={30}
            />
          </div>
          <button className="btn btn-primary" type="submit">
            저장
          </button>
          <button className="btn btn-secondary" type="button" onClick={() => setEditing(false)}>
            취소
          </button>
        </form>
      )}
    </section>
  );
}
