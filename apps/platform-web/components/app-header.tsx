"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { api, type Operator } from "@/lib/api";

const TEAMS: Record<string, string> = { CS: "CS팀", MARKETING: "마케팅팀", OPS: "운영팀" };

const MENU = [
  { href: "/admin/members", label: "회원" },
  { href: "/admin/orders", label: "주문" },
  { href: "/admin/inquiries", label: "1:1 문의" },
  { href: "/admin/db-token", label: "DB 접속" },
];

export function AppHeader({ me }: { me: Operator | null }) {
  const router = useRouter();
  const pathname = usePathname();

  async function logout() {
    await api("/admin/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/admin/login");
  }

  return (
    <header className="app-header">
      <div className="header-left">
        <div className="brand">
          <span className="brand-mark" />
          커머스 관리자
        </div>
        <nav className="menu">
          {MENU.map((m) => (
            <Link
              key={m.href}
              href={m.href}
              className={pathname.startsWith(m.href) ? "menu-item active" : "menu-item"}
            >
              {m.label}
            </Link>
          ))}
        </nav>
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
