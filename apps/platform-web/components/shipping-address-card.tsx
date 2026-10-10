"use client";

import { type FormEvent, useEffect, useState } from "react";

import {
  api,
  customerErrorMessage,
  fullAddress,
  type ShippingAddress,
  type ShippingAddressList,
} from "@/lib/api";

// 배송지 관리 (기능 레이어 7-4 ②) — 여러 개 등록, 기본 배송지 1개. 주문할 때 이 중에서 고른다
// 주소는 회원 정보가 아니라 배송지다 (policy 4-3). 고객 본인 행위라 접속기록 대상이 아니다
export function ShippingAddressCard() {
  const [data, setData] = useState<ShippingAddressList | null>(null);
  // null = 닫힘, "new" = 새로 등록, 숫자 = 그 배송지 수정
  const [editing, setEditing] = useState<"new" | number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ShippingAddressList>("/shop/me/addresses")
      .then(setData)
      .catch((e) => setError(customerErrorMessage(e)));
  }, []);

  async function call(path: string, init: RequestInit) {
    try {
      setData(await api<ShippingAddressList>(path, init));
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
      label: form.get("label"),
      recipient: form.get("recipient"),
      phone: form.get("phone"),
      zip_code: form.get("zip_code"),
      address: form.get("address"),
      address_detail: form.get("address_detail"),
      is_default: form.get("is_default") === "on",
    });
    if (editing === "new") call("/shop/me/addresses", { method: "POST", body });
    else call(`/shop/me/addresses/${editing}`, { method: "PUT", body });
  }

  function remove(item: ShippingAddress) {
    if (!window.confirm(`배송지 "${item.label}"을(를) 삭제할까요?`)) return;
    call(`/shop/me/addresses/${item.id}`, { method: "DELETE" });
  }

  const items = data?.items ?? [];
  const current = typeof editing === "number" ? items.find((i) => i.id === editing) : undefined;
  const full = data !== null && items.length >= data.max;

  return (
    <section className="card">
      <h2 className="card-title">배송지</h2>
      {error && <div className="alert-error">{error}</div>}
      {data && items.length === 0 && editing === null && (
        <p className="muted">등록한 배송지가 없습니다. 주문하려면 배송지를 먼저 등록하세요.</p>
      )}
      {items.length > 0 && (
        <ul className="address-list">
          {items.map((item) => (
            <li key={item.id} className="address-item">
              <div>
                <strong>{item.label}</strong>{" "}
                {item.is_default && <span className="badge badge-accent">기본</span>}
                <div>
                  {item.recipient} · {item.phone ?? "연락처 없음"}
                </div>
                <div className="muted">{fullAddress(item)}</div>
              </div>
              <div className="toolbar">
                {!item.is_default && (
                  <button
                    className="btn btn-secondary"
                    onClick={() => call(`/shop/me/addresses/${item.id}/default`, { method: "POST" })}
                  >
                    기본으로
                  </button>
                )}
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
          {full ? `배송지는 최대 ${data.max}개` : "배송지 추가"}
        </button>
      )}

      {editing !== null && (
        // key: 다른 배송지를 수정하러 바꿔도 입력칸이 새 값으로 채워지게
        <form key={String(editing)} className="stack" onSubmit={save}>
          <div className="field">
            <label htmlFor="addr_label">배송지 이름</label>
            <input
              id="addr_label"
              name="label"
              defaultValue={current?.label ?? ""}
              required
              maxLength={30}
            />
          </div>
          <div className="field">
            <label htmlFor="addr_recipient">받는 사람</label>
            <input
              id="addr_recipient"
              name="recipient"
              defaultValue={current?.recipient ?? ""}
              required
              maxLength={50}
            />
          </div>
          <div className="field">
            <label htmlFor="addr_phone">받는 사람 휴대전화번호</label>
            <input
              id="addr_phone"
              name="phone"
              type="tel"
              defaultValue={current?.phone ?? ""}
              placeholder="010-0000-0000"
              required
              maxLength={20}
            />
          </div>
          <div className="field">
            <label htmlFor="addr_zip">우편번호 (5자리)</label>
            <input
              id="addr_zip"
              name="zip_code"
              inputMode="numeric"
              defaultValue={current?.zip_code ?? ""}
              pattern="[0-9]{5}"
              required
              maxLength={5}
            />
          </div>
          <div className="field">
            <label htmlFor="addr_address">주소</label>
            <input
              id="addr_address"
              name="address"
              defaultValue={current?.address ?? ""}
              required
              maxLength={255}
            />
          </div>
          <div className="field">
            <label htmlFor="addr_detail">상세주소 (선택)</label>
            <input
              id="addr_detail"
              name="address_detail"
              defaultValue={current?.address_detail ?? ""}
              maxLength={100}
            />
          </div>
          {!current?.is_default && (
            <label className="check">
              <input type="checkbox" name="is_default" /> 기본 배송지로 지정
            </label>
          )}
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
