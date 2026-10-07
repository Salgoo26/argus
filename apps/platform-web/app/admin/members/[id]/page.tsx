"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import {
  ApiError,
  adminLoginPath,
  api,
  errorMessage,
  formatDate,
  formatDateTime,
  type Operator,
  type RefundAccountView,
  won,
} from "@/lib/api";

type MemberDetail = {
  id: number;
  name: string;
  email: string;
  phone: string | null;
  status: string;
  created_at: string;
  refund_account: RefundAccountView;
  orders: {
    id: number;
    product_name: string;
    amount: number;
    status: string;
    ordered_at: string;
    card_company: string | null;
  }[];
};

type Revealed = { bank_name: string; account_holder: string; account_number: string };

// 회원 상세 — 열면 접속기록(READ, 회원 기본정보)으로 남는다.
// 환불계좌는 끝 4자리만. "전체 보기"는 별도 요청이라 데이터 유형 "결제수단"으로 기록되고,
// Argus의 결제수단 조회 룰(상)에 매번 탐지된다 (기능 레이어 7 결정 7)
export default function MemberDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [member, setMember] = useState<MemberDetail | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
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
    api<MemberDetail>(`/admin/members/${id}`).then(setMember).catch(handleError);
  }, [id, handleError]);

  async function reveal() {
    const ok = window.confirm(
      "환불계좌 전체 번호를 조회합니다. 이 조회는 결제수단 조회로 기록되고 정보보호 담당자가 " +
        "소명을 요청합니다. 계속할까요?",
    );
    if (!ok) return;
    try {
      setRevealed(await api<Revealed>(`/admin/members/${id}/refund-account`));
    } catch (e) {
      handleError(e);
    }
  }

  const account = member?.refund_account ?? null;

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">회원 상세 {member && <span className="muted">#{member.id}</span>}</h1>
        <p className="page-subtitle">이 화면의 조회는 접속기록으로 남아 정보보호 담당자가 점검합니다.</p>
        {error && <div className="alert-error">{error}</div>}

        {member && (
          <>
            <section className="card">
              <h2 className="card-title">기본 정보</h2>
              <dl className="kv">
                <dt>이름</dt>
                <dd>{member.name}</dd>
                <dt>이메일</dt>
                <dd>{member.email}</dd>
                <dt>연락처</dt>
                <dd>{member.phone ?? "-"}</dd>
                <dt>가입일</dt>
                <dd>{formatDate(member.created_at)}</dd>
              </dl>
            </section>

            <section className="card">
              <h2 className="card-title">환불계좌</h2>
              {account ? (
                <div className="stack">
                  <div className="toolbar">
                    <span>
                      {account.bank_name} ****{account.account_last4} · 예금주 {account.account_holder}
                    </span>
                    {!revealed && (
                      <button className="btn btn-danger" onClick={reveal}>
                        전체 보기
                      </button>
                    )}
                  </div>
                  {revealed && (
                    <div className="reveal">
                      {revealed.bank_name} <span className="mono">{revealed.account_number}</span> ·
                      예금주 {revealed.account_holder}
                      <div className="hint">결제수단 조회로 기록되었습니다.</div>
                    </div>
                  )}
                </div>
              ) : (
                <p className="muted">등록된 환불계좌가 없습니다.</p>
              )}
            </section>

            <section className="card">
              <h2 className="card-title">최근 주문 (최대 20건)</h2>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th className="num">주문번호</th>
                      <th>상품</th>
                      <th className="num">금액</th>
                      <th>카드사</th>
                      <th>주문 일시</th>
                    </tr>
                  </thead>
                  <tbody>
                    {member.orders.map((o) => (
                      <tr key={o.id}>
                        <td className="num">{o.id}</td>
                        <td>{o.product_name}</td>
                        <td className="num">{won(o.amount)}</td>
                        <td>{o.card_company ?? "-"}</td>
                        <td>{formatDateTime(o.ordered_at)}</td>
                      </tr>
                    ))}
                    {member.orders.length === 0 && (
                      <tr>
                        <td colSpan={5} className="muted">
                          주문이 없습니다.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </>
  );
}
