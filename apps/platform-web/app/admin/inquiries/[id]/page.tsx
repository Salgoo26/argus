"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { ApiError, adminLoginPath, api, errorMessage, formatDateTime, INQUIRY_STATUS, type Operator } from "@/lib/api";

type InquiryDetail = {
  id: number;
  ticket_id: string;
  member_id: number | null;
  member_name: string | null;
  member_email: string | null;
  title: string;
  body: string;
  status: string;
  answer: string | null;
  answered_by_login_id: string | null;
  created_at: string;
  answered_at: string | null;
};

const MESSAGES: Record<string, string> = { ALREADY_ANSWERED: "이미 답변한 문의입니다." };

export default function AdminInquiryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [item, setItem] = useState<InquiryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace(adminLoginPath());
      else if (e instanceof ApiError && MESSAGES[e.code]) setError(MESSAGES[e.code]);
      else setError(errorMessage(e));
    },
    [router],
  );

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
    api<InquiryDetail>(`/admin/inquiries/${id}`).then(setItem).catch(handleError);
  }, [id, handleError]);

  async function answer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      setItem(
        await api<InquiryDetail>(`/admin/inquiries/${id}/answer`, {
          method: "POST",
          body: JSON.stringify({ answer: form.get("answer") }),
        }),
      );
      setError(null);
    } catch (e) {
      handleError(e);
    }
  }

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">
          문의 상세 {item && <span className="muted mono">{item.ticket_id}</span>}
        </h1>
        {error && <div className="alert-error">{error}</div>}
        {item && (
          <>
            <section className="card">
              <dl className="kv">
                <dt>상태</dt>
                <dd>
                  <span className={item.status === "ANSWERED" ? "badge badge-accent" : "badge"}>
                    {INQUIRY_STATUS[item.status]}
                  </span>
                </dd>
                <dt>작성자</dt>
                <dd>
                  {item.member_id ? (
                    <Link href={`/admin/members/${item.member_id}`}>
                      {item.member_name} ({item.member_id})
                    </Link>
                  ) : (
                    <span className="muted">탈퇴 회원</span>
                  )}
                </dd>
                <dt>접수 일시</dt>
                <dd>{formatDateTime(item.created_at)}</dd>
                <dt>제목</dt>
                <dd>{item.title}</dd>
              </dl>
              <p className="inquiry-body">{item.body}</p>
            </section>

            <section className="card">
              <h2 className="card-title">답변</h2>
              {item.answer ? (
                <div className="inquiry-answer">
                  <div className="hint">
                    {item.answered_by_login_id} ·{" "}
                    {item.answered_at ? formatDateTime(item.answered_at) : ""}
                  </div>
                  {item.answer}
                </div>
              ) : (
                <form className="stack" onSubmit={answer}>
                  {/* 답변은 고객에게 그대로 보인다 — 다른 고객 정보가 섞이지 않게 (v0.1 보강 M) */}
                  <p className="hint">고객 개인정보는 적지 마세요.</p>
                  <textarea name="answer" aria-label="답변" required maxLength={5000} />
                  <div>
                    <button className="btn btn-primary" type="submit">
                      답변 등록
                    </button>
                  </div>
                </form>
              )}
            </section>
          </>
        )}
      </main>
    </>
  );
}
