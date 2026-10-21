"use client";

import { type FormEvent, useEffect, useState } from "react";

import {
  api,
  customerErrorMessage,
  type RefundAccount,
  type RefundAccountList,
} from "@/lib/api";

// 환불계좌 관리 (커머스 확장 F7-1) — 배송지처럼 여러 개 등록해 두고, 무통장입금 주문을 취소·반품할 때
// 이 중에서 고른다. 서버는 계좌번호를 AES-256-GCM으로 암호화해 저장하고, 화면에는 끝 4자리만 돌려준다
export function RefundAccountCard() {
  const [data, setData] = useState<RefundAccountList | null>(null);
  // null = 닫힘, "new" = 새로 등록, 숫자 = 그 계좌 수정
  const [editing, setEditing] = useState<"new" | number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<RefundAccountList>("/shop/me/refund-accounts")
      .then(setData)
      .catch((e) => setError(customerErrorMessage(e)));
  }, []);

  async function call(path: string, init: RequestInit) {
    try {
      setData(await api<RefundAccountList>(path, init));
      setEditing(null);
      setError(null);
    } catch (e) {
      setError(customerErrorMessage(e));
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = JSON.stringify({
      bank_name: form.get("bank_name"),
      account_holder: form.get("account_holder"),
      account_number: form.get("account_number"),
    });
    if (editing === "new") call("/shop/me/refund-accounts", { method: "POST", body });
    else call(`/shop/me/refund-accounts/${editing}`, { method: "PUT", body });
  }

  function remove(item: RefundAccount) {
    if (!window.confirm(`${item.bank_name} ****${item.account_last4} 계좌를 삭제할까요?`)) return;
    call(`/shop/me/refund-accounts/${item.id}`, { method: "DELETE" });
  }

  const items = data?.items ?? [];
  const current = typeof editing === "number" ? items.find((i) => i.id === editing) : undefined;
  const full = data !== null && items.length >= data.max;

  return (
    <section className="card" id="refund-accounts">
      <h2 className="card-title">환불계좌</h2>
      {error && <div className="alert-error">{error}</div>}
      <p className="hint">무통장입금으로 결제한 주문을 취소·반품할 때 이 중에서 환불받을 계좌를 고릅니다.</p>
      {data && items.length === 0 && editing === null && (
        <p className="muted">등록한 환불계좌가 없습니다.</p>
      )}
      {items.length > 0 && (
        <ul className="address-list">
          {items.map((item) => (
            <li key={item.id} className="address-item">
              <div>
                <strong>
                  {item.bank_name} ****{item.account_last4}
                </strong>
                <div className="muted">예금주 {item.account_holder}</div>
              </div>
              <div className="toolbar">
                <button className="btn btn-secondary" onClick={() => setEditing(item.id)}>
                  수정
                </button>
                <button className="btn btn-danger" onClick={() => remove(item)}>
                  삭제
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {editing === null && data && (
        <button className="btn btn-secondary" onClick={() => setEditing("new")} disabled={full}>
          {full ? `환불계좌는 최대 ${data.max}개` : "환불계좌 추가"}
        </button>
      )}
      {editing !== null && data && (
        // key: 다른 계좌를 수정하러 바꿔도 입력칸이 새 값으로 채워지게
        <form key={String(editing)} className="stack" onSubmit={save}>
          <div className="field">
            <label htmlFor="bank_name">은행</label>
            <select id="bank_name" name="bank_name" defaultValue={current?.bank_name}>
              {data.banks.map((b) => (
                <option key={b}>{b}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="account_holder">예금주</label>
            <input
              id="account_holder"
              name="account_holder"
              defaultValue={current?.account_holder ?? ""}
              required
              maxLength={50}
            />
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
              placeholder={current ? `****${current.account_last4} — 다시 입력` : undefined}
            />
          </div>
          <div className="toolbar">
            <button className="btn btn-primary" type="submit">
              저장
            </button>
            <button className="btn btn-secondary" type="button" onClick={() => setEditing(null)}>
              취소
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
