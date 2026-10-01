"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { type RuleBody, RuleForm } from "@/components/rule-form";
import { ApiError, api } from "@/lib/api";
import type { Rule } from "@/lib/rules";

export default function NewRulePage() {
  const router = useRouter();
  const { me, handleError } = useMe();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function create(body: RuleBody) {
    setPending(true);
    setError(null);
    try {
      const rule = await api<Rule>("/rules", { method: "POST", body: JSON.stringify(body) });
      router.push(`/rules/${rule.id}`);
    } catch (e) {
      // 형식 오류는 서버가 어느 부분이 문제인지 알려 준다
      const detail = e instanceof ApiError && e.code === "INVALID_RULE" ? ` (${e.message})` : "";
      setError(`${handleError(e) ?? ""}${detail}`);
      setPending(false);
    }
  }

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <p>
          <Link href="/rules">← 룰 목록</Link>
        </p>
        <h1 className="page-title">새 룰 만들기</h1>
        {me && me.role !== "OFFICER" && <div className="alert-error">정보보호 담당자 전용 화면입니다.</div>}
        {error && <div className="alert-error">{error}</div>}
        {me?.role === "OFFICER" && (
          <section className="card">
            <RuleForm initial={null} submitLabel="룰 만들기" pending={pending} onSubmit={create} />
          </section>
        )}
      </main>
    </>
  );
}
