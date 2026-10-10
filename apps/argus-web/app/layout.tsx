import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Argus — 접속기록 점검",
  description: "개인정보 접속기록 이상행위 탐지·소명 관리",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko">
      <body>
        {children}
      </body>
    </html>
  );
}
