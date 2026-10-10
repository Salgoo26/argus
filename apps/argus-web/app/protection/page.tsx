"use client";

import { useCallback, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api } from "@/lib/api";
import { DATA_CATEGORY_LABELS, formatDateTime } from "@/lib/labels";

// 보호 대상 등록부 (v0.1 보강 N) — 정보보호 담당자 전용. DB 직접 접근(2티어) 기록·탐지의 기준표
// 게이트웨이가 보낸 DB 구조 목록에서 컬럼을 골라 분류한다. 모든 변경은 사유와 함께 이력에 남는다

type ColumnStatus = "PERSONAL" | "NOT_PERSONAL" | "UNCLASSIFIED" | "RELEASED" | "MISSING";
type Column = {
  column_name: string;
  id: number | null;
  data_type: string | null;
  item: string | null;
  data_category: string | null;
  member_key: boolean;
  active: boolean;
  status: ColumnStatus;
};
type Table = {
  table_name: string;
  data_category: string | null;
  db_access_30d: number;
  last_db_access_at: string | null;
  columns: Column[];
};
type Overview = {
  version: string;
  database: string;
  summary: {
    unclassified: number;
    personal_columns: number;
    personal_tables: number;
    missing: number;
    last_schema_at: string | null;
    two_year_retention: boolean;
    two_year_items: string[];
  };
  tables: Table[];
};
type HistoryItem = {
  id: number;
  version: string;
  change_type: "REGISTER" | "CHANGE" | "RELEASE";
  snapshot: { table_name: string; column_name: string; item: string; data_category: string | null };
  reason: string;
  changed_by: string | null;
  changed_at: string;
};

const ITEMS: Record<string, string> = {
  NAME: "이름",
  EMAIL: "이메일",
  PHONE: "연락처",
  ADDRESS: "주소",
  BIRTH: "생년월일",
  ACCOUNT: "계좌번호",
  CARD: "카드번호",
  UNIQUE_ID: "고유식별정보",
  SENSITIVE: "민감정보",
  CREDENTIAL: "인증정보",
  MEMBER_ID: "회원 식별자",
  OTHER: "기타",
  NOT_PERSONAL: "개인정보 아님",
};
const CATEGORIES = ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"];
const STATUS_LABELS: Record<ColumnStatus, string> = {
  PERSONAL: "개인정보",
  NOT_PERSONAL: "개인정보 아님",
  UNCLASSIFIED: "미분류",
  RELEASED: "해제",
  MISSING: "DB에 없음",
};
const CHANGES: Record<string, string> = { REGISTER: "등록", CHANGE: "변경", RELEASE: "해제" };

function statusClass(status: ColumnStatus): string {
  if (status === "PERSONAL") return "badge badge-accent";
  if (status === "UNCLASSIFIED" || status === "MISSING") return "badge badge-warn";
  return "badge";
}

function ColumnRow({
  table,
  column,
  onChanged,
  onError,
}: {
  table: string;
  column: Column;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [item, setItem] = useState(column.item ?? "NOT_PERSONAL");
  const [category, setCategory] = useState(column.data_category ?? "MEMBER_BASIC");
  const [memberKey, setMemberKey] = useState(column.member_key);
  const [reason, setReason] = useState("");
  const personal = item !== "NOT_PERSONAL";

  async function save() {
    try {
      await api("/protection/columns", {
        method: "POST",
        body: JSON.stringify({
          table_name: table,
          column_name: column.column_name,
          item,
          data_category: personal ? category : null,
          member_key: item === "MEMBER_ID" && memberKey,
          reason,
        }),
      });
      setReason("");
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  async function release() {
    if (!column.id) return;
    try {
      await api(`/protection/columns/${column.id}/release`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setReason("");
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  return (
    <tr>
      <td className="mono">{column.column_name}</td>
      <td className="muted small">{column.data_type ?? "-"}</td>
      <td>
        <span className={statusClass(column.status)}>{STATUS_LABELS[column.status]}</span>
      </td>
      <td>
        {column.data_type ? (
          <select value={item} onChange={(e) => setItem(e.target.value)}>
            {Object.entries(ITEMS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        ) : (
          (ITEMS[column.item ?? ""] ?? "-")
        )}
      </td>
      <td>
        {column.data_type && personal ? (
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {DATA_CATEGORY_LABELS[c]}
              </option>
            ))}
          </select>
        ) : (
          "-"
        )}
      </td>
      <td>
        {item === "MEMBER_ID" && column.data_type ? (
          <input
            type="checkbox"
            aria-label="회원 식별 열"
            checked={memberKey}
            onChange={(e) => setMemberKey(e.target.checked)}
          />
        ) : column.member_key ? (
          "예"
        ) : (
          "-"
        )}
      </td>
      <td>
        <div className="toolbar">
          <input
            aria-label="사유"
            placeholder="사유"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
          />
          {column.data_type && (
            <button className="btn btn-secondary" disabled={!reason.trim()} onClick={save}>
              {column.active ? "변경" : "등록"}
            </button>
          )}
          {column.active && (
            <button className="btn btn-danger" disabled={!reason.trim()} onClick={release}>
              해제
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function History({ onError }: { onError: (e: unknown) => void }) {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  useEffect(() => {
    api<{ items: HistoryItem[] }>("/protection/history")
      .then((r) => setItems(r.items))
      .catch(onError);
  }, [onError]);
  return (
    <section className="card">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>일시</th>
              <th>버전</th>
              <th>구분</th>
              <th>컬럼</th>
              <th>항목</th>
              <th>사유</th>
              <th>처리자</th>
            </tr>
          </thead>
          <tbody>
            {items?.map((h) => (
              <tr key={h.id}>
                <td>{formatDateTime(h.changed_at)}</td>
                <td className="mono">{h.version}</td>
                <td>{CHANGES[h.change_type]}</td>
                <td className="mono">
                  {h.snapshot.table_name}.{h.snapshot.column_name}
                </td>
                <td>{ITEMS[h.snapshot.item] ?? h.snapshot.item}</td>
                <td>{h.reason}</td>
                <td>{h.changed_by ?? "시스템"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ProtectionPage() {
  const { me, handleError } = useMe();
  const [data, setData] = useState<Overview | null>(null);
  const [tab, setTab] = useState<"registry" | "history">("registry");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onError = useCallback((e: unknown) => setError(handleError(e)), [handleError]);
  const load = useCallback(() => {
    api<Overview>("/protection").then(setData).catch(onError);
  }, [onError]);
  useEffect(load, [load]);

  const summary = data?.summary;
  const tables = (data?.tables ?? []).filter(
    (t) => !onlyOpen || t.columns.some((c) => c.status === "UNCLASSIFIED" || c.status === "MISSING"),
  );

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">보호 대상</h1>
        {error && <div className="alert-error">{error}</div>}

        {summary && data && (
          <section className="card">
            <dl className="summary-grid">
              <div>
                <dt>개인정보 테이블 / 컬럼</dt>
                <dd>
                  {summary.personal_tables}개 / {summary.personal_columns}개
                </dd>
              </div>
              <div>
                <dt>미분류 컬럼</dt>
                <dd>
                  {summary.unclassified > 0 ? (
                    <span className="badge badge-warn">{summary.unclassified}개</span>
                  ) : (
                    "0개"
                  )}
                </dd>
              </div>
              <div>
                <dt>DB 구조 목록 수신</dt>
                <dd>{formatDateTime(summary.last_schema_at)}</dd>
              </div>
              <div>
                <dt>접속기록 보관</dt>
                <dd>
                  {summary.two_year_retention ? (
                    <span className="badge badge-danger">
                      최소 2년 ({summary.two_year_items.map((i) => ITEMS[i]).join("·")} 처리)
                    </span>
                  ) : (
                    "최소 1년"
                  )}
                </dd>
              </div>
              <div>
                <dt>등록부 버전</dt>
                <dd className="mono">{data.version}</dd>
              </div>
            </dl>
            <p className="muted small" style={{ margin: "8px 0 0" }}>
              화면 경유 접속기록은 각 화면에 지정된 데이터 유형으로 수집됩니다. 이 등록부는 DB 직접 접근 기록에
              쓰입니다.
            </p>
          </section>
        )}

        <div className="tabs">
          <button
            className={tab === "registry" ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab("registry")}
          >
            등록부
          </button>
          <button
            className={tab === "history" ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab("history")}
          >
            등록 이력
          </button>
          {tab === "registry" && (
            <label className="small" style={{ alignSelf: "center" }}>
              <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> 미분류가
              있는 테이블만
            </label>
          )}
        </div>

        {tab === "history" ? (
          <History onError={onError} />
        ) : (
          tables.map((t) => (
            <section className="card" key={t.table_name}>
              <h2 className="card-title">
                <span className="mono">
                  {data?.database}.{t.table_name}
                </span>{" "}
                {t.data_category ? (
                  <span className="badge badge-accent">{DATA_CATEGORY_LABELS[t.data_category]}</span>
                ) : (
                  <span className="badge">개인정보 없음</span>
                )}{" "}
                <span className="muted small">
                  최근 30일 DB 직접 접근 {t.db_access_30d}건
                  {t.last_db_access_at && ` · 마지막 ${formatDateTime(t.last_db_access_at)}`}
                </span>
              </h2>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>컬럼</th>
                      <th>자료형</th>
                      <th>분류</th>
                      <th>개인정보 항목</th>
                      <th>데이터 유형</th>
                      <th>회원 식별 열</th>
                      <th>등록·해제</th>
                    </tr>
                  </thead>
                  <tbody>
                    {t.columns.map((c) => (
                      <ColumnRow
                        key={`${c.column_name}-${c.id}-${c.status}-${c.item}`}
                        table={t.table_name}
                        column={c}
                        onChanged={load}
                        onError={onError}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))
        )}
      </main>
    </>
  );
}
