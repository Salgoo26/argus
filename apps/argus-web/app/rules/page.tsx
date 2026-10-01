"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api } from "@/lib/api";
import { SEVERITY_LABELS, formatDateTime } from "@/lib/labels";
import { RULE_TYPE_LABELS, type RuleSummary, describeRule, severityBadgeClass } from "@/lib/rules";

// 삭제는 없다 — 쓰지 않는 룰은 끄고, 기본 목록은 켜진 룰만 보여 준다 (2026-10-01 결정)
const FILTERS = [
  { key: "true", label: "켜짐" },
  { key: "false", label: "꺼짐" },
  { key: "", label: "전체" },
];

export default function RulesPage() {
  const router = useRouter();
  const { me, handleError } = useMe();
  const [enabled, setEnabled] = useState("true");
  const [items, setItems] = useState<RuleSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isOfficer = me?.role === "OFFICER";

  useEffect(() => {
    if (!isOfficer) return;
    const query = enabled ? `?enabled=${enabled}` : "";
    api<{ items: RuleSummary[] }>(`/rules${query}`)
      .then((page) => setItems(page.items))
      .catch((e) => setError(handleError(e)));
  }, [enabled, isOfficer, handleError]);

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <div className="title-row">
          <h1 className="page-title">탐지 룰</h1>
          {isOfficer && (
            <Link className="btn btn-primary" href="/rules/new">
              새 룰 만들기
            </Link>
          )}
        </div>
        <p className="page-subtitle">
          탐지 배치가 접속기록을 판정하는 기준입니다. 고친 룰은 다음 순찰부터 적용되고, 모든 변경은
          이력으로 남습니다. 룰은 지우지 않고 꺼서 관리합니다.
        </p>

        {me && !isOfficer && <div className="alert-error">정보보호 담당자 전용 화면입니다.</div>}
        {error && <div className="alert-error">{error}</div>}

        <div className="tabs">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              className={`tab ${f.key === enabled ? "tab-active" : ""}`}
              onClick={() => setEnabled(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>

        <section className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>룰</th>
                  <th>유형</th>
                  <th>조건</th>
                  <th>심각도</th>
                  <th>상태</th>
                  <th className="num">버전</th>
                  <th className="num">탐지건</th>
                  <th>최근 변경</th>
                </tr>
              </thead>
              <tbody>
                {items?.map((rule) => (
                  <tr key={rule.id} className="clickable" onClick={() => router.push(`/rules/${rule.id}`)}>
                    <td>{rule.name}</td>
                    <td>{RULE_TYPE_LABELS[rule.rule_type]}</td>
                    <td className="subjects" style={{ minWidth: 240 }}>
                      {describeRule(rule)}
                    </td>
                    <td>
                      <span className={severityBadgeClass(rule.severity)}>
                        {SEVERITY_LABELS[rule.severity]}
                      </span>
                    </td>
                    <td>
                      <span className={rule.enabled ? "badge badge-accent" : "badge"}>
                        {rule.enabled ? "켜짐" : "꺼짐"}
                      </span>
                    </td>
                    <td className="num">v{rule.version}</td>
                    <td className="num">{rule.detection_count}</td>
                    <td className="muted">
                      {formatDateTime(rule.updated_at)} · {rule.updated_by ?? "시스템"}
                    </td>
                  </tr>
                ))}
                {items && items.length === 0 && (
                  <tr>
                    <td colSpan={8} className="muted" style={{ textAlign: "center", padding: 24 }}>
                      해당하는 룰이 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </>
  );
}
