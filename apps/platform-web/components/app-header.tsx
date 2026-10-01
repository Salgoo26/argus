"use client";

import { useRouter } from "next/navigation";

import { api, type Operator } from "@/lib/api";

const TEAMS: Record<string, string> = { CS: "CS팀", MARKETING: "마케팅팀", OPS: "운영팀" };

export function AppHeader({ me }: { me: Operator | null }) {
  const router = useRouter();

  async function logout() {
    await api("/admin/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-mark" />
        커머스 관리자
      </div>
      {me && (
        <div className="header-user">
          <span>
            {me.name} ({me.login_id}) · {TEAMS[me.team] ?? me.team}
          </span>
          <button className="btn btn-secondary" onClick={logout}>
            로그아웃
          </button>
        </div>
      )}
    </header>
  );
}
