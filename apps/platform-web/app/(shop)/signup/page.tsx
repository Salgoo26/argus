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

const LOGIN_ID = /^[a-z][a-z0-9]{3,19}$/;
const CARRIERS = ["SKT", "KT", "LGU+", "SKT알뜰폰", "KT알뜰폰", "LGU+알뜰폰"];

type EmailVerified = { email: string; token: string };
type IdentityVerified = { name: string; phone: string; birth_date: string; token: string };

// 회원가입 (커머스 확장) — 아이디 중복 확인 → 이메일 인증 → 휴대폰 본인확인 → 동의 → 가입.
// 이름·휴대폰은 본인확인 결과로 정해져 직접 입력하지 않는다.
// 필수·선택 동의를 따로 받는다 (PIPA §22①·⑤). "전체 동의" 버튼은 두지 않는다:
// 선택 항목까지 한 번에 체크하게 유도하지 않기 위함 (명확한 의사 표시)
export default function SignupPage() {
  const router = useRouter();
  const [items, setItems] = useState<ConsentItem[]>([]);
  const [agreed, setAgreed] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const [loginId, setLoginId] = useState("");
  const [checkedId, setCheckedId] = useState<string | null>(null); // 중복 확인을 통과한 아이디
  const [idMessage, setIdMessage] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [emailVerified, setEmailVerified] = useState<EmailVerified | null>(null);
  const [identity, setIdentity] = useState<IdentityVerified | null>(null);
  const [identityOpen, setIdentityOpen] = useState(false);

  useEffect(() => {
    api<ConsentItem[]>("/shop/consent-items")
      .then(setItems)
      .catch((e) => setError(customerErrorMessage(e)));
  }, []);

  const missingRequired = items.some((item) => item.required && !agreed[item.code]);
  const idReady = checkedId !== null && checkedId === loginId.trim().toLowerCase();
  const emailReady = emailVerified !== null && emailVerified.email === email.trim().toLowerCase();
  const ready = idReady && emailReady && identity !== null && !missingRequired;

  async function checkLoginId() {
    const value = loginId.trim().toLowerCase();
    setIdMessage(null);
    if (!LOGIN_ID.test(value)) {
      setCheckedId(null);
      return setIdMessage("영문 소문자로 시작하는 영문 소문자·숫자 4~20자로 입력하세요.");
    }
    try {
      const res = await api<{ login_id: string; available: boolean }>(
        `/shop/auth/login-id-available?login_id=${encodeURIComponent(value)}`,
      );
      setCheckedId(res.available ? res.login_id : null);
      setIdMessage(res.available ? "사용할 수 있는 아이디입니다." : "이미 있는 아이디입니다.");
    } catch (e) {
      setIdMessage(customerErrorMessage(e));
    }
  }

  // 데모라 실제 메일을 보내지 않는다 — 인증 버튼을 누르면 바로 인증 완료
  async function verifyEmail() {
    setError(null);
    try {
      const res = await api<EmailVerified>("/shop/verifications/email", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setEmailVerified(res);
    } catch (e) {
      setEmailVerified(null);
      setError(customerErrorMessage(e));
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const problem = passwordProblem(password);
    if (problem) return setError(problem);
    if (password !== form.get("password_confirm")) {
      return setError("비밀번호 확인이 일치하지 않습니다.");
    }
    if (!emailVerified || !identity) return;
    setPending(true);
    setError(null);
    try {
      await api("/shop/auth/signup", {
        method: "POST",
        body: JSON.stringify({
          login_id: checkedId,
          password,
          email_token: emailVerified.token,
          identity_token: identity.token,
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
        {error && <div className="alert-error">{error}</div>}
        <form className="stack" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="login_id">아이디</label>
            <div className="input-row">
              <input
                id="login_id"
                autoComplete="username"
                autoCapitalize="none"
                required
                maxLength={20}
                value={loginId}
                onChange={(e) => setLoginId(e.target.value)}
              />
              <button type="button" className="btn btn-secondary" onClick={checkLoginId}>
                중복 확인
              </button>
            </div>
            <span className={idReady ? "hint hint-ok" : "hint"}>
              {idMessage ?? "영문 소문자로 시작, 영문 소문자·숫자 4~20자"}
            </span>
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
            <label htmlFor="email">이메일</label>
            <div className="input-row">
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                maxLength={255}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={verifyEmail}
                disabled={!email || emailReady}
              >
                {emailReady ? "인증 완료" : "인증"}
              </button>
            </div>
            <span className={emailReady ? "hint hint-ok" : "hint"}>
              {emailReady ? "이메일 인증을 마쳤습니다." : "주문·문의 안내를 받을 이메일"}
            </span>
          </div>
          <div className="field">
            <span className="field-label">휴대폰 본인확인</span>
            {identity ? (
              <dl className="kv">
                <dt>이름</dt>
                <dd>{identity.name}</dd>
                <dt>휴대전화번호</dt>
                <dd>{identity.phone}</dd>
              </dl>
            ) : (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setIdentityOpen(true)}
              >
                휴대폰 본인확인
              </button>
            )}
            <span className="hint">이름·휴대전화번호는 본인확인 결과로 등록됩니다.</span>
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

          <button className="btn btn-primary" type="submit" disabled={pending || !ready}>
            {pending ? "가입 중…" : "가입하기"}
          </button>
        </form>
      </div>

      {identityOpen && (
        <IdentityWindow
          onVerified={(result) => {
            setIdentity(result);
            setIdentityOpen(false);
          }}
          onCancel={() => setIdentityOpen(false)}
        />
      )}
    </div>
  );
}

// ── 가상 본인확인 창 ─────────────────────────────────────────

// 실제 서비스라면 본인확인기관(통신사 등)의 창이다. 쇼핑몰은 주민등록번호를 처리할 수 없어
// (개인정보 보호법 제24조의2) 기관 창에만 입력하고, 쇼핑몰은 결과만 받는다.
// 이 창도 주민번호 앞 7자리를 **이 컴포넌트의 상태에만** 두고 생년월일·성별·내외국인으로 바꿔
// 보낸다 — 번호 자체는 어떤 요청에도 실리지 않는다(<form>·name 속성 없음, PG 결제창과 같은 구조).
// 데모라 입력하고 인증 버튼을 누르면 바로 인증된다. 본인확인 이용 동의는 이 창에서 받는다.
function IdentityWindow({
  onVerified,
  onCancel,
}: {
  onVerified: (result: IdentityVerified) => void;
  onCancel: () => void;
}) {
  const [carrier, setCarrier] = useState(CARRIERS[0]);
  const [name, setName] = useState("");
  const [birth6, setBirth6] = useState("");
  const [genderDigit, setGenderDigit] = useState("");
  const [phone, setPhone] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function verify() {
    const parsed = parseResidentPrefix(birth6, genderDigit);
    if (!parsed) return setError("주민등록번호 앞 7자리를 확인하세요.");
    setPending(true);
    setError(null);
    // 인증 요청 직전에 지운다 — 요청에는 변환한 결과만 실린다
    setBirth6("");
    setGenderDigit("");
    try {
      const result = await api<IdentityVerified>("/shop/verifications/identity", {
        method: "POST",
        body: JSON.stringify({ carrier, name, phone, agreed, ...parsed }),
      });
      onVerified(result);
    } catch (e) {
      setError(customerErrorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="overlay">
      <div className="card pg-window identity-window" role="dialog" aria-label="휴대폰 본인확인">
        <div className="pg-head">데모 본인확인 서비스</div>
        <div className="pg-warning" role="alert">
          가상 본인확인 — 실제 주민등록번호 입력 금지
        </div>
        <div className="field">
          <label htmlFor="idv_carrier">통신사</label>
          <select id="idv_carrier" value={carrier} onChange={(e) => setCarrier(e.target.value)}>
            {CARRIERS.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="idv_name">이름</label>
          <input
            id="idv_name"
            autoComplete="off"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={50}
          />
        </div>
        <div className="field">
          <label htmlFor="idv_birth">주민등록번호 앞 7자리</label>
          <div className="input-row rrn">
            <input
              id="idv_birth"
              inputMode="numeric"
              autoComplete="off"
              placeholder="생년월일 6자리"
              value={birth6}
              onChange={(e) => setBirth6(e.target.value.replace(/\D/g, ""))}
              maxLength={6}
            />
            <span>-</span>
            <input
              aria-label="주민등록번호 뒷자리 첫 숫자"
              inputMode="numeric"
              autoComplete="off"
              value={genderDigit}
              onChange={(e) => setGenderDigit(e.target.value.replace(/\D/g, ""))}
              maxLength={1}
            />
            <span className="mono">●●●●●●</span>
          </div>
        </div>
        <div className="field">
          <label htmlFor="idv_phone">휴대전화번호</label>
          <input
            id="idv_phone"
            type="tel"
            autoComplete="off"
            placeholder="010-0000-0000"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            maxLength={20}
          />
        </div>
        <label className="check">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
          <span className="badge badge-accent">필수</span>
          본인확인 서비스 이용 동의 (개인정보 이용·고유식별정보 처리·통신사 이용약관)
        </label>
        {error && <div className="alert-error">{error}</div>}
        <div className="toolbar">
          <button
            className="btn btn-primary"
            onClick={verify}
            disabled={pending || !agreed || !name || !phone}
          >
            {pending ? "인증 중…" : "인증"}
          </button>
          <button className="btn btn-secondary" onClick={onCancel} disabled={pending}>
            취소
          </button>
        </div>
      </div>
    </div>
  );
}

// 주민번호 앞 7자리 → 생년월일·성별·내외국인 (본인확인기관이 돌려주는 결과 형태)
// 뒷자리 첫 숫자: 1·2(1900년대 내국인), 3·4(2000년대 내국인), 5·6(1900년대 외국인), 7·8(2000년대 외국인)
function parseResidentPrefix(birth6: string, digit: string) {
  if (!/^\d{6}$/.test(birth6) || !/^[1-8]$/.test(digit)) return null;
  const n = Number(digit);
  const century = n === 1 || n === 2 || n === 5 || n === 6 ? 1900 : 2000;
  const year = century + Number(birth6.slice(0, 2));
  const month = Number(birth6.slice(2, 4));
  const day = Number(birth6.slice(4, 6));
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return {
    birth_date: `${year}-${birth6.slice(2, 4)}-${birth6.slice(4, 6)}`,
    gender: n % 2 === 1 ? "M" : "F",
    is_foreigner: n >= 5,
  };
}
