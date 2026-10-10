"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { api, can, type Operator, type Permission } from "@/lib/api";

const TEAMS: Record<string, string> = { CS: "CS팀", MARKETING: "마케팅팀", OPS: "운영팀" };

// 역할에 없는 메뉴는 숨긴다 (v0.1 보강 L-1) — 서버도 403으로 막는다
const MENU: { href: string; label: string; permission: Permission }[] = [
  { href: "/admin/members", label: "회원", permission: "MEMBERS" },
  { href: "/admin/orders", label: "주문", permission: "ORDERS" },
  { href: "/admin/inquiries", label: "1:1 문의", permission: "INQUIRIES" },
  { href: "/admin/db-token", label: "DB 직접 접근", permission: "DB_TOKEN" },
  { href: "/admin/accounts", label: "계정·권한", permission: "ACCOUNTS" },
];

const PASSWORD_PAGE = "/admin/password";

export function AppHeader({ me }: { me: Operator | null }) {
  const router = useRouter();
  const pathname = usePathname();

  // 임시 비밀번호 계정은 비밀번호를 바꾸기 전까지 다른 화면을 쓸 수 없다 (서버도 403)
  useEffect(() => {
    if (me?.must_change_password && pathname !== PASSWORD_PAGE) router.replace(PASSWORD_PAGE);
  }, [me, pathname, router]);

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
        {me && !me.must_change_password && (
          <nav className="menu">
            {MENU.filter((m) => can(me, m.permission)).map((m) => (
              <Link
                key={m.href}
                href={m.href}
                className={pathname.startsWith(m.href) ? "menu-item active" : "menu-item"}
              >
                {m.label}
              </Link>
            ))}
          </nav>
        )}
      </div>
      {me && (
        <div className="header-user">
          <span>
            <Link href={PASSWORD_PAGE}>
              {me.name} ({me.login_id})
            </Link>{" "}
            · {TEAMS[me.team] ?? me.team}
          </span>
          <button className="btn btn-secondary" onClick={logout}>
            로그아웃
          </button>
        </div>
      )}
    </header>
  );
}
