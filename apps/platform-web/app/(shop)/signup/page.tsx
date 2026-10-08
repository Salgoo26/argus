"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import {
  api,
  type ConsentItem,
  customerErrorMessage,
  passwordProblem,
  safeShopPath,
} from "@/lib/api";

// 회원가입 — 필수·선택 동의를 따로 받는다 (PIPA §22①·⑤). "전체 동의" 버튼은 두지 않는다:
// 선택 항목까지 한 번에 체크하게 유도하지 않기 위함 (명확한 의사 표시)
export default function SignupPage() {
  const router = useRouter();
  const [items, setItems] = useState<ConsentItem[]>([]);
  const [agreed, setAgreed] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    api<ConsentItem[]>("/shop/consent-items")
      .then(setItems)
      .catch((e) => setError(customerErrorMessage(e)));
  }, []);

  const missingRequired = items.some((item) => item.required && !agreed[item.code]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const problem = passwordProblem(password);
    if (problem) return setError(problem);
    if (password !== form.get("password_confirm")) {
      return setError("비밀번호 확인이 일치하지 않습니다.");
    }
    setPending(true);
    setError(null);
    try {
      await api("/shop/auth/signup", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password,
          name: form.get("name"),
          phone: form.get("phone"),
          consents: Object.fromEntries(items.map((item) => [item.code, !!agreed[item.code]])),
        }),
      });
      // 주문하다 가입하러 왔으면 그 상품으로 (7-4 ③), 아니면 마이페이지
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next ? safeShopPath(next) : "/mypage");
    } catch (e) {
      setError(customerErrorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="narrow">
      <div className="card">
        <h1 className="page-title">회원가입</h1>
        <p className="page-subtitle">
          가입에 꼭 필요한 정보(이메일·비밀번호·이름·휴대전화번호)만 받습니다. 생년월일·성별은 받지
          않으며, 배송지는 필요할 때 마이페이지에서 등록합니다.
        </p>
        {error && <div className="alert-error">{error}</div>}
        <form className="stack" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="email">이메일 (로그인 아이디)</label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              required
              maxLength={255}
            />
          </div>
          <div className="field">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              maxLength={256}
            />
            <span className="hint">10자 이상, 영문·숫자·특수문자 중 2종류 이상</span>
          </div>
          <div className="field">
            <label htmlFor="password_confirm">비밀번호 확인</label>
            <input
              id="password_confirm"
              name="password_confirm"
              type="password"
              autoComplete="new-password"
              required
              maxLength={256}
            />
          </div>
          <div className="field">
            <label htmlFor="name">이름</label>
            <input id="name" name="name" autoComplete="name" required maxLength={50} />
          </div>
          <div className="field">
            <label htmlFor="phone">휴대전화번호</label>
            <input
              id="phone"
              name="phone"
              type="tel"
              autoComplete="tel"
              placeholder="010-0000-0000"
              required
              maxLength={20}
            />
            <span className="hint">주문·배송 연락에 씁니다</span>
          </div>

          <fieldset className="consents">
            <legend>약관 및 동의</legend>
            {items.map((item) => (
              <div key={item.code} className="consent">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={!!agreed[item.code]}
                    onChange={(e) => setAgreed((a) => ({ ...a, [item.code]: e.target.checked }))}
                  />
                  <span className={item.required ? "badge badge-accent" : "badge"}>
                    {item.required ? "필수" : "선택"}
                  </span>
                  {item.name}
                </label>
                {/* §15② — 목적·항목·기간을 동의받기 전에 알린다 */}
                <dl className="consent-detail">
                  <dt>목적</dt>
                  <dd>{item.purpose}</dd>
                  <dt>항목</dt>
                  <dd>{item.items}</dd>
                  <dt>보유 기간</dt>
                  <dd>{item.retention}</dd>
                </dl>
                {item.code === "TOS" && (
                  <Link href="/terms" target="_blank" className="hint">
                    이용약관 전문 보기
                  </Link>
                )}
                {item.code === "PRIVACY_REQUIRED" && (
                  <Link href="/privacy" target="_blank" className="hint">
                    개인정보 처리방침 보기
                  </Link>
                )}
              </div>
            ))}
            <p className="hint">
              동의를 거부할 권리가 있습니다. 선택 항목은 동의하지 않아도 가입할 수 있고, 필수 항목에
              동의하지 않으면 가입할 수 없습니다.
            </p>
          </fieldset>

          <button className="btn btn-primary" type="submit" disabled={pending || missingRequired}>
            {pending ? "가입 중…" : "가입하기"}
          </button>
        </form>
      </div>
    </div>
  );
}
