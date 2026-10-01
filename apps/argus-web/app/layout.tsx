import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Argus — 접속기록 점검",
  description: "개인정보 접속기록 이상행위 탐지·소명 관리 (모든 데이터는 가상)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko">
      <body>
        {/* CLAUDE.md 3절 #2 — 실제 개인정보 금지. 화면에서도 가상 데이터임을 밝힌다 */}
        <div className="notice">
          데모 환경입니다. 화면의 회원·직원 정보와 접속기록은 모두 가상으로 생성된 데이터입니다.
        </div>
        {children}
      </body>
    </html>
  );
}
