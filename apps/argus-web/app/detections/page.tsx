"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { DueLabel } from "@/components/due-label";
import { api, type CaseSearch, type CaseSummary, type Status } from "@/lib/api";
import {
  PATH_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  formatDateTime,
  pathBadgeClass,
  statusBadgeClass,
} from "@/lib/labels";
import { type RuleSummary, severityBadgeClass } from "@/lib/rules";

type CasePage = { items: CaseSummary[]; page: number; size: number; total: number };

const PAGE_SIZE = 20;
const FILTERS: (Status | null)[] = [null, "DETECTED", "REQUESTED", "SUBMITTED", "REJECTED", "APPROVED", "DISMISSED", "ESCALATED"];
const FORM_KEYS = ["date_from", "date_to", "rule_id", "actor", "severity", "access_path", "sort"] as const;

// 이번 달 1일 ~ 오늘 (한국 날짜) — 담당자 화면 기본 기간. 보고서와 같은 기준(탐지 시각의 한국 날짜)
function thisMonth(): { date_from: string; date_to: string } {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" }); // YYYY-MM-DD
  return { date_from: `${today.slice(0, 8)}01`, date_to: today };
}

// 검색 조건은 화면 상태에만 두고 URL에 싣지 않는다 — 서버에도 요청 본문(POST)으로 보낸다
// (취급자 아이디가 서버 접근 로그·브라우저 방문 기록에 남지 않게, v0.1 보강 C-1)
function toCriteria(form: FormData, status: Status | undefined): CaseSearch {
  const criteria: CaseSearch = { page: 1, size: PAGE_SIZE };
  if (status) criteria.status = status;
  for (const key of FORM_KEYS) {
    const value = String(form.get(key) ?? "").trim();
    if (!value) continue;
    (criteria as Record<string, unknown>)[key] = key === "rule_id" ? Number(value) : value;
  }
  return criteria;
}

export default function DetectionsPage() {
  const router = useRouter();
  const { me, handleError } = useMe();
  const isOfficer = me?.role === "OFFICER";
  const role = me?.role ?? null;
  // 첫 조건 — 담당자는 이번 달(점검 대상), 취급자는 기간 제한 없음(진행 중인 요청을 놓치지 않게)
  const initial = useMemo<CaseSearch | null>(
    () => (role ? { page: 1, size: PAGE_SIZE, ...(role === "OFFICER" ? thisMonth() : {}) } : null),
    [role],
  );
  const [chosen, setCriteria] = useState<CaseSearch | null>(null);
  const criteria = chosen ?? initial;
  const [rules, setRules] = useState<RuleSummary[]>([]);
  const [data, setData] = useState<CasePage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (role !== "OFFICER") return;
    api<{ items: RuleSummary[] }>("/rules")
      .then((page) => setRules(page.items))
      .catch(() => setRules([])); // 룰 목록이 없어도 다른 조건으로는 검색할 수 있다
  }, [role]);

  useEffect(() => {
    if (!criteria) return;
    // 목록 조회도 Argus 자체 접속기록(READ)으로 남는다 — 조건의 이름만, 값은 남지 않는다
    api<CasePage>("/detections/search", { method: "POST", body: JSON.stringify(criteria) })
      .then((page) => {
        setData(page);
        setError(null);
      })
      .catch((e) => setError(handleError(e)));
  }, [criteria, handleError]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCriteria(toCriteria(new FormData(event.currentTarget), criteria?.status));
  }

  function setStatus(status: Status | null) {
    const next: CaseSearch = { ...(criteria ?? { size: PAGE_SIZE }), page: 1 };
    if (status) next.status = status;
    else delete next.status;
    setCriteria(next);
  }

  const page = criteria?.page ?? 1;
  const goTo = (p: number) => criteria && setCriteria({ ...criteria, page: p });
  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const defaults = isOfficer ? thisMonth() : { date_from: "", date_to: "" };

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">{isOfficer ? "탐지건" : "내 소명 요청"}</h1>
        <p className="page-subtitle">
          {isOfficer
            ? "탐지 룰에 걸린 접속기록입니다. 탐지 즉시 해당 취급자에게 소명이 자동 요청됩니다 — 오탐이면 요청을 취소하세요."
            : "본인 접속기록 중 소명이 요청된 건입니다. 행위 사유를 작성해 제출하세요."}
        </p>

        {error && <div className="alert-error">{error}</div>}

        {me && (
          <section className="card">
            {/* key: 역할이 정해진 뒤 그 역할의 기본값으로 그린다 */}
            <form key={me.role} className="toolbar" onSubmit={onSearch}>
              <div className="field">
                <label htmlFor="date_from">탐지일 시작</label>
                <input id="date_from" name="date_from" type="date" defaultValue={defaults.date_from} />
              </div>
              <div className="field">
                <label htmlFor="date_to">탐지일 끝</label>
                <input id="date_to" name="date_to" type="date" defaultValue={defaults.date_to} />
              </div>
              {isOfficer && (
                <>
                  <div className="field">
                    <label htmlFor="rule_id">룰</label>
                    <select id="rule_id" name="rule_id" defaultValue="">
                      <option value="">전체</option>
                      {rules.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="actor">취급자(아이디)</label>
                    <input id="actor" name="actor" placeholder="ops_park" maxLength={64} size={12} autoComplete="off" />
                  </div>
                </>
              )}
              <div className="field">
                <label htmlFor="severity">심각도</label>
                <select id="severity" name="severity" defaultValue="">
                  <option value="">전체</option>
                  <option value="HIGH">{SEVERITY_LABELS.HIGH}</option>
                  <option value="MEDIUM">{SEVERITY_LABELS.MEDIUM}</option>
                  <option value="LOW">{SEVERITY_LABELS.LOW}</option>
                </select>
              </div>
              {/* 경로별로 따로 점검·보고한다 (policy 1-5) */}
              <div className="field">
                <label htmlFor="access_path">접근 경로</label>
                <select id="access_path" name="access_path" defaultValue="">
                  <option value="">전체</option>
                  <option value="APP">{PATH_LABELS.APP}</option>
                  <option value="DB">{PATH_LABELS.DB}</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="sort">정렬</label>
                <select id="sort" name="sort" defaultValue="LATEST">
                  <option value="LATEST">최신순</option>
                  <option value="SEVERITY">심각도순</option>
                </select>
              </div>
              <button className="btn btn-primary" type="submit">
                검색
              </button>
            </form>
            <p className="muted" style={{ margin: "12px 0 0", fontSize: 12 }}>
              기간은 탐지 시각의 한국 날짜(양 끝 포함) — 점검 보고서와 같은 기준입니다.
              {isOfficer ? " 기본은 이번 달입니다." : " 비우면 전체 기간입니다."}
            </p>
          </section>
        )}

        <div className="filter-row">
          <div className="tabs">
            {FILTERS.map((s) => (
              <button
                key={s ?? "ALL"}
                className={`tab ${s === (criteria?.status ?? null) ? "tab-active" : ""}`}
                onClick={() => setStatus(s)}
              >
                {s ? STATUS_LABELS[s] : "전체"}
              </button>
            ))}
          </div>
        </div>

        <section className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">번호</th>
                  <th>룰</th>
                  <th>심각도</th>
                  <th>경로</th>
                  <th>취급자</th>
                  <th>발생일</th>
                  <th className="num">기록</th>
                  <th className="num">처리 건수</th>
                  <th>상태</th>
                  <th className="num">차수</th>
                  <th>탐지 시각</th>
                  <th>소명 기한</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => router.push(`/detections/${c.id}`)}>
                    <td className="num">#{c.id}</td>
                    <td>{c.rule_name}</td>
                    <td>
                      <span className={severityBadgeClass(c.severity)}>
                        {SEVERITY_LABELS[c.severity]}
                      </span>
                    </td>
                    <td>
                      <span className={pathBadgeClass(c.access_path)}>{PATH_LABELS[c.access_path]}</span>
                    </td>
                    <td>
                      {c.actor_name ?? "-"} <span className="muted">({c.actor_login_id})</span>
                    </td>
                    <td>{c.group_bucket.replace("T", " ").replace("+09:00", "")}</td>
                    <td className="num">{c.log_count}</td>
                    <td className="num">{c.subject_count_sum ?? "-"}</td>
                    <td>
                      <span className={statusBadgeClass(c.status)}>{STATUS_LABELS[c.status]}</span>
                    </td>
                    <td className="num">{c.round}</td>
                    <td>{formatDateTime(c.detected_at)}</td>
                    <td>{c.due_at ? <DueLabel due={c.due_at} /> : "-"}</td>
                  </tr>
                ))}
                {data && data.items.length === 0 && (
                  <tr>
                    <td colSpan={12} className="muted" style={{ textAlign: "center", padding: 24 }}>
                      해당하는 탐지건이 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button className="btn btn-secondary" disabled={page <= 1} onClick={() => goTo(page - 1)}>
              이전
            </button>
            <span className="muted">
              {page} / {lastPage} · 총 {data?.total ?? "-"}건
            </span>
            <button className="btn btn-secondary" disabled={page >= lastPage} onClick={() => goTo(page + 1)}>
              다음
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
