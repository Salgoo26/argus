"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type Customer } from "@/lib/api";

export function ShopHeader() {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Customer | null>(null);

  // 화면을 옮길 때마다 로그인 상태를 다시 확인한다 (로그인·로그아웃·탈퇴 직후 반영)
  useEffect(() => {
    api<Customer>("/shop/me")
      .then(setMe)
      .catch(() => setMe(null));
  }, [pathname]);

  async function logout() {
    await api("/shop/auth/logout", { method: "POST" }).catch(() => undefined);
    setMe(null);
    router.replace("/");
  }

  return (
    <header className="app-header">
      <Link href="/" className="brand">
        <span className="brand-mark" />
        데모 커머스
      </Link>
      <nav className="header-user">
        {me ? (
          <>
            <span>{me.name}님</span>
            <Link href="/mypage" className="btn btn-secondary">
              마이페이지
            </Link>
            <button className="btn btn-secondary" onClick={logout}>
              로그아웃
            </button>
          </>
        ) : (
          <>
            <Link href="/login" className="btn btn-secondary">
              로그인
            </Link>
            <Link href="/signup" className="btn btn-primary">
              회원가입
            </Link>
          </>
        )}
      </nav>
    </header>
  );
}
