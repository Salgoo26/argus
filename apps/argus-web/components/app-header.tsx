"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, errorMessage, type Me } from "@/lib/api";

const ROLE_LABELS = { OFFICER: "정보보호 담당자", HANDLER: "개인정보취급자" } as const;

/** 로그인한 사용자 — 세션이 없으면 로그인 화면으로 보낸다 */
export function useMe(): { me: Me | null; handleError: (e: unknown) => string | null } {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  const handleError = useCallback(
    (e: unknown): string | null => {
      if (e instanceof ApiError && e.status === 401) {
        router.replace("/login");
        return null;
      }
      return errorMessage(e);
    },
    [router],
  );

  useEffect(() => {
    api<Me>("/auth/me")
      .then(setMe)
      .catch((e) => handleError(e));
  }, [handleError]);

  return { me, handleError };
}

// 담당자 메뉴 — 탐지건(결재함)과 접속기록(원장 검색)은 하는 일이 달라 화면을 나눈다.
// 취급자는 자기 소명 건만 보므로 메뉴가 없다 (메뉴 숨김은 편의, 권한 판단은 서버)
const OFFICER_MENU = [
  { href: "/detections", label: "탐지건" },
  { href: "/access-logs", label: "접속기록" },
];

export function AppHeader({ me }: { me: Me | null }) {
  const router = useRouter();
  const pathname = usePathname();

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  return (
    <header className="app-header">
      <Link href="/detections" className="brand" style={{ color: "inherit", textDecoration: "none" }}>
        <span className="brand-mark" />
        Argus
        <span className="muted" style={{ fontWeight: 400 }}>
          접속기록 점검
        </span>
      </Link>
      {me?.role === "OFFICER" && (
        <nav className="header-nav">
          {OFFICER_MENU.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={pathname.startsWith(item.href) ? "nav-active" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      )}
      {me && (
        <div className="header-user">
          <span>
            {me.name ?? me.login_id} ({me.login_id}) · {ROLE_LABELS[me.role]}
          </span>
          <button className="btn btn-secondary" onClick={logout}>
            로그아웃
          </button>
        </div>
      )}
    </header>
  );
}
