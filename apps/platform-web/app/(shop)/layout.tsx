import Link from "next/link";

import { ShopHeader } from "@/components/shop-header";

// 고객 화면 공통 틀 — 고객(정보주체) 행위는 접속기록 대상이 아니다 (CLAUDE.md 3절 #4)
export default function ShopLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="shop">
      <ShopHeader />
      <main className="container shop-main">{children}</main>
      <footer className="shop-footer">
        <div className="container shop-footer-inner">
          {/* 처리방침은 다른 고지와 구분되게 눈에 띄게 (PIPA §30②, 표준지침) */}
          <Link href="/privacy" className="privacy-link">
            개인정보 처리방침
          </Link>
          <Link href="/terms">이용약관</Link>
          <span className="muted">데모 커머스</span>
        </div>
      </footer>
    </div>
  );
}
