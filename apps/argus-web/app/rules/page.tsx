"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api } from "@/lib/api";
import { SEVERITY_LABELS, formatDateTime } from "@/lib/labels";
import {
  ACCESS_PATH_LABELS,
  RULE_TYPE_LABELS,
  type RuleSummary,
  describeRule,
  severityBadgeClass,
} from "@/lib/rules";

// 삭제는 없다 — 쓰지 않는 룰은 끄고, 기본 목록은 켜진 룰만 보여 준다 (2026-10-01 결정)
const FILTERS = [
  { key: "true", label: "켜짐" },
  { key: "false", label: "꺼짐" },
  { key: "", label: "전체" },
];

// 이름(부분 일치)·적용 경로·심각도·유형 (v0.1 보강 C-3) — 룰 정의는 개인정보가 아니라 URL 쿼리로
const FILTER_KEYS = ["name", "access_path", "severity", "rule_type"] as const;
type RuleFilters = Partial<Record<(typeof FILTER_KEYS)[number], string>>;

export default function RulesPage() {
  const router = useRouter();
  const { me, handleError } = useMe();
  const [enabled, setEnabled] = useState("true");
  const [filters, setFilters] = useState<RuleFilters>({});
  const [items, setItems] = useState<RuleSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isOfficer = me?.role === "OFFICER";

  useEffect(() => {
    if (!isOfficer) return;
    const params = new URLSearchParams();
    if (enabled) params.set("enabled", enabled);
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    const query = params.size > 0 ? `?${params}` : "";
    api<{ items: RuleSummary[] }>(`/rules${query}`)
      .then((page) => setItems(page.items))
      .catch((e) => setError(handleError(e)));
  }, [enabled, filters, isOfficer, handleError]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next: RuleFilters = {};
    for (const key of FILTER_KEYS) {
      const value = String(form.get(key) ?? "").trim();
      if (value) next[key] = value;
    }
    setFilters(next);
  }

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

        {isOfficer && (
          <section className="card">
            <form className="toolbar" onSubmit={onSearch} onReset={() => setFilters({})}>
              <div className="field">
                <label htmlFor="name">이름</label>
                <input id="name" name="name" placeholder="야간" maxLength={100} size={14} />
              </div>
              <div className="field">
                <label htmlFor="access_path">적용 경로</label>
                <select id="access_path" name="access_path" defaultValue="">
                  <option value="">전체</option>
                  {(["APP", "DB", "ALL"] as const).map((p) => (
                    <option key={p} value={p}>
                      {p === "ALL" ? "두 경로 모두" : ACCESS_PATH_LABELS[p]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="severity">심각도</label>
                <select id="severity" name="severity" defaultValue="">
                  <option value="">전체</option>
                  {(["HIGH", "MEDIUM", "LOW"] as const).map((s) => (
                    <option key={s} value={s}>
                      {SEVERITY_LABELS[s]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="rule_type">유형</label>
                <select id="rule_type" name="rule_type" defaultValue="">
                  <option value="">전체</option>
                  {(["EVENT", "AGGREGATE"] as const).map((t) => (
                    <option key={t} value={t}>
                      {RULE_TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
              </div>
              <button className="btn btn-primary" type="submit">
                검색
              </button>
              <button className="btn btn-secondary" type="reset">
                초기화
              </button>
            </form>
          </section>
        )}

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
