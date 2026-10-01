import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "데모 커머스 — Argus 데모 플랫폼",
  description: "Argus가 감시하는 데모 커머스 (모든 데이터는 가상)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko">
      <body>
        {/* CLAUDE.md 3절 #2 — 실제 개인정보 금지. 화면에서도 가상 데이터임을 밝힌다 */}
        <div className="notice">
          데모 환경입니다. 화면의 회원·직원 정보는 모두 가상으로 생성된 데이터입니다. 실제 개인정보를
          입력하지 마세요.
        </div>
        {children}
      </body>
    </html>
  );
}
