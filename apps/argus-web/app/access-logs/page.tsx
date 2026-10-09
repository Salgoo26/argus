"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { DbStatement, SubjectsCell } from "@/components/db-statement";
import { api, type AccessLogPage, type AccessLogSearch } from "@/lib/api";
import {
  ACTION_LABELS,
  DATA_CATEGORY_LABELS,
  PATH_LABELS,
  SOURCE_LABELS,
  formatDateTime,
  pathBadgeClass,
} from "@/lib/labels";

const PAGE_SIZE = 50;
const ACTIONS = ["LOGIN", "LOGOUT", "READ", "CREATE", "UPDATE", "DELETE", "DOWNLOAD", "EXPORT", "UNMASK"];
const CATEGORIES = ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY", "ACCESS_LOG", "NONE"];
const EMPTY: AccessLogSearch = { source: "PLATFORM", page: 1, size: PAGE_SIZE };
const KEYS = [
  "actor",
  "date_from",
  "date_to",
  "action",
  "subject",
  "access_path",
  "client_ip",
  "data_category",
  "result",
] as const;

// 검색 조건은 화면 상태에만 두고 URL(주소창·방문 기록)에 싣지 않는다 — 회원번호 검색어가 남지 않게.
// 서버에도 요청 본문(POST)으로 보낸다 (argus-api access_logs/router.py)
function toCriteria(form: FormData): AccessLogSearch {
  const criteria: AccessLogSearch = { ...EMPTY };
  for (const key of KEYS) {
    const value = String(form.get(key) ?? "").trim();
    if (value) (criteria as Record<string, unknown>)[key] = value;
  }
  criteria.source = form.get("source") === "ARGUS" ? "ARGUS" : "PLATFORM";
  return criteria;
}

// 서버의 기간은 [시작일 0시, 종료일 다음날 0시) — 화면에는 양 끝 포함 날짜로 보여 준다
function periodLabel(period: { from: string; to: string }): string {
  const kstDate = (ms: number) =>
    new Date(ms).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
  return `${kstDate(Date.parse(period.from))} ~ ${kstDate(Date.parse(period.to) - 1)}`;
}

export default function AccessLogsPage() {
  const { me, handleError } = useMe();
  const [criteria, setCriteria] = useState<AccessLogSearch>(EMPTY);
  const [data, setData] = useState<AccessLogPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 마지막으로 응답을 받은 조건 — 지금 조건과 다르면 요청 중이다
  const [settled, setSettled] = useState<AccessLogSearch | null>(null);

  const isOfficer = me?.role === "OFFICER";
  const loading = me !== null && settled !== criteria;

  useEffect(() => {
    if (!me) return;
    // 취급자는 같은 API로 본인 기록만 받는다 — 행위자는 서버가 고정 (v0.1 보강 D)
    // 검색도 Argus 자체 접속기록(READ)으로 남는다 — 조건의 이름만, 검색어 값은 남지 않는다
    api<AccessLogPage>("/access-logs/search", { method: "POST", body: JSON.stringify(criteria) })
      .then((page) => {
        setData(page);
        setError(null);
      })
      .catch((e) => setError(handleError(e)))
      .finally(() => setSettled(criteria));
  }, [criteria, handleError, me]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCriteria(toCriteria(new FormData(event.currentTarget)));
  }

  // 입력칸은 브라우저가 비우고(type="reset"), 결과는 기본 조건으로 다시 가져온다
  const onReset = () => setCriteria(EMPTY);

  const lastPage = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const goTo = (page: number) => setCriteria((c) => ({ ...c, page }));

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">{me && !isOfficer ? "내 접속기록" : "접속기록"}</h1>
        <p className="page-subtitle">
          {me && !isOfficer
            ? "플랫폼에서 내가 한 개인정보 처리 기록입니다(화면 경유·DB 직접). 소명을 쓸 때 근거로 확인하세요. 정보주체는 마스킹되어 표시되고, 이 조회도 접속기록으로 남습니다."
            : "원장에 쌓인 접속기록을 조건으로 찾아봅니다 — 정기 점검(§8②)과 정보주체 열람 청구 대응용. 정보주체는 마스킹되어 표시되고, 이 검색도 접속기록으로 남습니다."}
        </p>

        <section className="card">
          <form className="toolbar" onSubmit={onSearch} onReset={onReset}>
            {isOfficer && (
              <div className="field">
                <label htmlFor="actor">계정</label>
                <input id="actor" name="actor" placeholder="ops_park" maxLength={64} size={12} />
              </div>
            )}
            <div className="field">
              <label htmlFor="date_from">시작일</label>
              <input id="date_from" name="date_from" type="date" />
            </div>
            <div className="field">
              <label htmlFor="date_to">종료일</label>
              <input id="date_to" name="date_to" type="date" />
            </div>
            <div className="field">
              <label htmlFor="action">수행업무</label>
              <select id="action" name="action" defaultValue="">
                <option value="">전체</option>
                {ACTIONS.map((a) => (
                  <option key={a} value={a}>
                    {ACTION_LABELS[a] ?? a}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="subject">정보주체(회원번호)</label>
              <input
                id="subject"
                name="subject"
                placeholder="10293"
                maxLength={64}
                size={12}
                autoComplete="off"
              />
            </div>
            <div className="field">
              <label htmlFor="access_path">접근 경로</label>
              <select id="access_path" name="access_path" defaultValue="">
                <option value="">전체</option>
                <option value="APP">{PATH_LABELS.APP}</option>
                <option value="DB">{PATH_LABELS.DB}</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="client_ip">접속지(IP)</label>
              <input
                id="client_ip"
                name="client_ip"
                placeholder="10.20.3 또는 전체 주소"
                maxLength={45}
                size={14}
                pattern="[0-9A-Fa-f:.]+"
                title="숫자·점·콜론만 — 앞부분만 적으면 앞부분 일치"
                autoComplete="off"
              />
            </div>
            <div className="field">
              <label htmlFor="data_category">데이터 유형</label>
              <select id="data_category" name="data_category" defaultValue="">
                <option value="">전체</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c === "NONE" ? "없음(로그인 등)" : (DATA_CATEGORY_LABELS[c] ?? c)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="result">결과</label>
              <select id="result" name="result" defaultValue="">
                <option value="">전체</option>
                <option value="SUCCESS">성공</option>
                <option value="FAILURE">실패</option>
              </select>
            </div>
            {isOfficer && (
              <div className="field">
                <label htmlFor="source">출처</label>
                <select id="source" name="source" defaultValue="PLATFORM">
                  <option value="PLATFORM">플랫폼</option>
                  <option value="ARGUS">Argus 자체</option>
                </select>
              </div>
            )}
            <button className="btn btn-primary" type="submit" disabled={loading}>
              검색
            </button>
            <button className="btn btn-secondary" type="reset" disabled={loading}>
              초기화
            </button>
          </form>
          <p className="muted" style={{ margin: "12px 0 0", fontSize: 12 }}>
            기간을 비우면 최근 7일(한국 날짜), 최대 1년까지 검색합니다. 한 기록의 정보주체가
            1,000명을 넘으면 앞 1,000명만 저장되어 회원번호 검색에서 빠질 수 있습니다.
          </p>
        </section>

        {data && !data.linked && (
          <div className="alert-error">
            계정이 취급자 명부와 연결되어 있지 않아 본인 접속기록을 찾을 수 없습니다. 정보보호
            담당자에게 문의하세요.
          </div>
        )}
        {error && <div className="alert-error">{error}</div>}

        <section className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>발생 시각</th>
                  <th>출처</th>
                  <th>경로</th>
                  <th>계정</th>
                  <th>접속지</th>
                  <th>수행업무</th>
                  <th>데이터</th>
                  <th>결과</th>
                  <th>기능 / SQL</th>
                  <th className="num">처리 건수</th>
                  <th>정보주체</th>
                  <th>탐지건</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((log) => (
                  <tr key={log.id}>
                    <td>{formatDateTime(log.occurred_at)}</td>
                    <td>
                      {SOURCE_LABELS[log.source] ?? log.source}
                    </td>
                    <td>
                      <span className={pathBadgeClass(log.access_path)}>{PATH_LABELS[log.access_path]}</span>
                    </td>
                    <td>
                      {log.actor_name ?? "-"} <span className="muted">({log.actor_login_id})</span>
                    </td>
                    <td>{log.client_ip}</td>
                    <td>{ACTION_LABELS[log.action] ?? log.action}</td>
                    <td>{DATA_CATEGORY_LABELS[log.data_category] ?? log.data_category}</td>
                    <td>
                      <span className={log.result === "FAILURE" ? "badge badge-danger" : "badge"}>
                        {log.result === "FAILURE" ? "실패" : "성공"}
                      </span>
                    </td>
                    <td className="muted">
                      {log.db ? (
                        <DbStatement db={log.db} />
                      ) : (
                        <>
                          {log.request_method} {log.request_path}
                        </>
                      )}
                    </td>
                    <td className="num">
                      {log.subject_count}
                      {log.subject_truncated && <span className="muted"> (일부 저장)</span>}
                    </td>
                    <td className="subjects">
                      <SubjectsCell subjects={log.subjects} count={log.subject_count} db={log.db} />
                    </td>
                    <td>
                      {log.detection_ids.map((id) => (
                        <Link key={id} href={`/detections/${id}`} style={{ marginRight: 6 }}>
                          #{id}
                        </Link>
                      ))}
                    </td>
                  </tr>
                ))}
                {data && data.items.length === 0 && (
                  <tr>
                    <td colSpan={12} className="muted" style={{ textAlign: "center", padding: 24 }}>
                      조건에 맞는 접속기록이 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button
              className="btn btn-secondary"
              disabled={loading || criteria.page <= 1}
              onClick={() => goTo(criteria.page - 1)}
            >
              이전
            </button>
            <span className="muted">
              {criteria.page} / {lastPage} · 총 {data?.total ?? "-"}건
              {data?.period && ` · 기간 ${periodLabel(data.period)}`}
            </span>
            <button
              className="btn btn-secondary"
              disabled={loading || criteria.page >= lastPage}
              onClick={() => goTo(criteria.page + 1)}
            >
              다음
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
