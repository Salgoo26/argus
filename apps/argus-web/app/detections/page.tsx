"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api, type CaseSummary, type Status } from "@/lib/api";
import {
  type AccessPath,
  PATH_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  formatDateTime,
  pathBadgeClass,
  statusBadgeClass,
} from "@/lib/labels";
import { severityBadgeClass } from "@/lib/rules";

type CasePage = { items: CaseSummary[]; page: number; size: number; total: number };

const PAGE_SIZE = 20;
const FILTERS: (Status | null)[] = [null, "DETECTED", "REQUESTED", "SUBMITTED", "REJECTED", "APPROVED", "DISMISSED", "ESCALATED"];

export default function DetectionsPage() {
  const router = useRouter();
  const { me, handleError } = useMe();
  const [status, setStatus] = useState<Status | null>(null);
  const [path, setPath] = useState<AccessPath | null>(null);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CasePage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) });
    if (status) params.set("status", status);
    if (path) params.set("access_path", path);
    // 목록 조회도 Argus 자체 접속기록(READ)으로 남는다 — 목록엔 회원 식별값이 없어 건수 0
    api<CasePage>(`/detections?${params}`)
      .then(setData)
      .catch((e) => setError(handleError(e)));
  }, [status, path, page, handleError]);

  const isOfficer = me?.role === "OFFICER";
  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

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

        <div className="filter-row">
        <div className="tabs">
          {FILTERS.map((s) => (
            <button
              key={s ?? "ALL"}
              className={`tab ${s === status ? "tab-active" : ""}`}
              onClick={() => {
                setStatus(s);
                setPage(1);
              }}
            >
              {s ? STATUS_LABELS[s] : "전체"}
            </button>
          ))}
        </div>
          {/* 경로별로 따로 점검·보고한다 (policy 1-5) */}
          <label className="inline-field">
            접근 경로{" "}
            <select
              value={path ?? ""}
              onChange={(e) => {
                setPath((e.target.value || null) as AccessPath | null);
                setPage(1);
              }}
            >
              <option value="">전체</option>
              <option value="APP">{PATH_LABELS.APP}</option>
              <option value="DB">{PATH_LABELS.DB}</option>
            </select>
          </label>
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
                  </tr>
                ))}
                {data && data.items.length === 0 && (
                  <tr>
                    <td colSpan={11} className="muted" style={{ textAlign: "center", padding: 24 }}>
                      해당하는 탐지건이 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button className="btn btn-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              이전
            </button>
            <span className="muted">
              {page} / {lastPage} · 총 {data?.total ?? "-"}건
            </span>
            <button
              className="btn btn-secondary"
              disabled={page >= lastPage}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
