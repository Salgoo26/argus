"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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

export function AppHeader({ me }: { me: Me | null }) {
  const router = useRouter();

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
