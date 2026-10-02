"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  api,
  customerErrorMessage,
  formatDateTime,
  INQUIRY_STATUS,
  type Inquiry,
} from "@/lib/api";

// 1:1 문의 (PLT-06) — 이 문의가 CS가 내 정보를 조회하는 업무 근거가 된다
export default function MyInquiriesPage() {
  const router = useRouter();
  const [items, setItems] = useState<Inquiry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(customerErrorMessage(e));
    },
    [router],
  );

  useEffect(() => {
    api<{ items: Inquiry[] }>("/shop/inquiries")
      .then((body) => setItems(body.items))
      .catch(handleError);
  }, [handleError]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    setPending(true);
    try {
      const created = await api<Inquiry>("/shop/inquiries", {
        method: "POST",
        body: JSON.stringify({ title: form.get("title"), body: form.get("body") }),
      });
      setItems((list) => [created, ...(list ?? [])]);
      formEl.reset();
      setError(null);
    } catch (e) {
      handleError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <h1 className="page-title">1:1 문의</h1>
      <p className="page-subtitle">
        상담원이 문의를 처리하면서 회원님의 주문·회원 정보를 확인할 수 있습니다. 문의에는 비밀번호·
        카드번호를 적지 마세요.
      </p>
      {error && <div className="alert-error">{error}</div>}

      <section className="card">
        <h2 className="card-title">문의하기</h2>
        <form className="stack" onSubmit={submit}>
          <div className="field">
            <label htmlFor="title">제목</label>
            <input id="title" name="title" required maxLength={200} />
          </div>
          <div className="field">
            <label htmlFor="body">내용</label>
            <textarea id="body" name="body" required maxLength={5000} />
          </div>
          <div>
            <button className="btn btn-primary" type="submit" disabled={pending}>
              {pending ? "등록 중…" : "문의 등록"}
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <h2 className="card-title">내 문의</h2>
        {items?.length === 0 && <p className="muted">문의 내역이 없습니다.</p>}
        <div className="stack">
          {items?.map((q) => (
            <article key={q.id} className="inquiry">
              <div className="inquiry-head">
                <span className={q.status === "ANSWERED" ? "badge badge-accent" : "badge"}>
                  {INQUIRY_STATUS[q.status]}
                </span>
                <strong>{q.title}</strong>
                <span className="muted">{formatDateTime(q.created_at)}</span>
              </div>
              <p className="inquiry-body">{q.body}</p>
              {q.answer && (
                <div className="inquiry-answer">
                  <div className="hint">
                    답변 {q.answered_at ? formatDateTime(q.answered_at) : ""}
                  </div>
                  {q.answer}
                </div>
              )}
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
