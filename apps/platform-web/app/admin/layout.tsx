import type { Metadata } from "next";

// 관리자 화면(/admin) — 운영에서는 Caddy가 이 경로를 허용 IP로 제한한다 (architecture 7-2)
export const metadata: Metadata = {
  title: "커머스 관리자",
  description: "커머스 관리자",
};

export default function AdminLayout({ children }: LayoutProps<"/admin">) {
  return children;
}
