"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { NotificationBell } from "@/components/notifications";
import { releaseBrowserPush, syncBrowserPush } from "@/components/push-toggle";
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
// 취급자는 내 소명 요청과 내 접속기록(v0.1 보강 D)만 (메뉴 숨김은 편의, 권한 판단은 서버)
const MENUS = {
  OFFICER: [
    { href: "/detections", label: "행위 탐지" },
    { href: "/access-logs", label: "접속기록" },
    { href: "/rules", label: "룰 설정" },
    { href: "/reports", label: "점검 보고서" },
    { href: "/protection", label: "보호 대상" },
    { href: "/users", label: "계정" },
  ],
  HANDLER: [
    { href: "/detections", label: "내 소명 요청" },
    { href: "/access-logs", label: "내 접속기록" },
  ],
} as const;

export function AppHeader({ me }: { me: Me | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const loginId = me?.login_id ?? null;

  // 브라우저에 남은 웹 푸시 구독을 지금 로그인한 계정과 맞춘다 (계정마다 한 번)
  useEffect(() => {
    if (loginId) syncBrowserPush(loginId);
  }, [loginId]);

  async function logout() {
    // 브라우저 쪽 웹 푸시 구독도 해제 — 서버는 로그아웃 요청에서 구독을 지운다
    await releaseBrowserPush();
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
      {me && (
        <nav className="header-nav">
          {MENUS[me.role].map((item) => (
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
          <NotificationBell />
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
