import type { Metadata } from "next";

export const metadata: Metadata = { title: "이용약관 — 데모 커머스" };

// 이용약관 — 자리표시 문안. 정식 문안은 Cowork 기획 방에서 작성해 교체한다
export default function TermsPage() {
  return (
    <article className="card doc">
      <h1 className="page-title">이용약관</h1>
      <div className="alert-info">
        자리표시 문안입니다. 정식 문안은 기획 단계에서 작성해 교체할 예정입니다.
      </div>
      <p className="muted">시행일: 2026-10-01 (v1)</p>

      <h2>제1조 (목적)</h2>
      <p>
        이 약관은 데모 커머스(가상 회사)가 제공하는 데모 서비스의 이용 조건을 정합니다. 이 서비스는
        포트폴리오 시연용이며 실제 상품 거래가 일어나지 않습니다.
      </p>

      <h2>제2조 (회원가입과 탈퇴)</h2>
      <p>
        만 14세 이상인 사람이 약관과 개인정보 수집·이용에 동의하면 가입할 수 있습니다. 회원은
        언제든지 마이페이지에서 탈퇴할 수 있습니다.
      </p>

      <h2>제3조 (금지 행위)</h2>
      <p>실제 개인정보(본인 또는 타인의 실명·연락처·주소 등)를 입력하지 마십시오.</p>
    </article>
  );
}
