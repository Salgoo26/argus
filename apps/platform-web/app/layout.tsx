import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "데모 커머스",
  description: "데모 커머스",
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
