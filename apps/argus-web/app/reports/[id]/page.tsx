"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api, type Report, type ReportCase, type ReportSection } from "@/lib/api";
import {
  ACTION_LABELS,
  type AccessPath,
  DATA_CATEGORY_LABELS,
  PATH_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  formatDateTime,
  statusBadgeClass,
} from "@/lib/labels";
import { SCOPE_LABELS, periodLabel, subjectsLabel } from "@/lib/reports";
import { severityBadgeClass } from "@/lib/rules";

// 숫자 묶음 표시 — {READ: 12, DOWNLOAD: 3} → "조회 12 · 다운로드 3"
function counts(values: Record<string, number>, labels: Record<string, string>): string {
  const entries = Object.entries(values);
  if (entries.length === 0) return "-";
  return entries.map(([key, n]) => `${labels[key] ?? key} ${n}`).join(" · ");
}

const REVIEW_LABELS = { APPROVED: "승인", REJECTED: "반려" } as const;

type Anchor = NonNullable<Report["summary"]["integrity"]["previous"]>;

// 직전 보고서의 마지막 기록이 같은 해시로 남아 있는가 (v0.1 보강 I — 끝부분 삭제 확인)
function AnchorCheck({ previous }: { previous: Anchor }) {
  if (previous.status === "NONE") return <span className="muted">비교 대상 없음</span>;
  return (
    <>
      {previous.status === "MATCH" ? (
        <span className="badge badge-accent">일치</span>
      ) : (
        <span className="badge badge-danger">불일치</span>
      )}{" "}
      <span className="muted">
        보고서 #{previous.report_id} · 기록 #{previous.last_id}
      </span>
    </>
  );
}

function hasHandling(c: ReportCase): boolean {
  return Boolean(c.explanation || c.handled_by || c.close_reason);
}

// 탐지건별 처리 내용 (v0.1 보강 B) — 소명 요지·검토·티켓·첨부·처리 담당자, 요청 취소 사유
function Handling({ c }: { c: ReportCase }) {
  const e = c.explanation;
  return (
    <dl className="handling">
      {e && (
        <div>
          <dt>소명 ({e.round}차)</dt>
          <dd>
            {e.content ?? <span className="muted">미제출</span>}
            {e.ticket_ids.length > 0 && <span className="muted"> · 티켓 {e.ticket_ids.join(", ")}</span>}
            {e.attachment_count > 0 && <span className="muted"> · 첨부 {e.attachment_count}개</span>}
          </dd>
        </div>
      )}
      {e?.review_result && (
        <div>
          <dt>검토</dt>
          <dd>
            {REVIEW_LABELS[e.review_result]}
            {e.review_comment && ` — ${e.review_comment}`}
          </dd>
        </div>
      )}
      {c.close_reason && (
        <div>
          <dt>요청 취소 사유</dt>
          <dd>{c.close_reason}</dd>
        </div>
      )}
      {c.handled_by && (
        <div>
          <dt>처리</dt>
          <dd>
            {c.handled_by}
            {c.handled_at && <span className="muted"> · {formatDateTime(c.handled_at)}</span>}
          </dd>
        </div>
      )}
    </dl>
  );
}

function Section({ path, section }: { path: AccessPath; section: ReportSection }) {
  const { logs, detections, explanations } = section;
  return (
    <section className="card report-section">
      <h2 className="card-title">{PATH_LABELS[path]}</h2>

      <h3 className="report-h3">접속기록</h3>
      <dl className="summary-grid">
        <div>
          <dt>기록 / 행위자</dt>
          <dd>
            {logs.total}건 / {logs.actors}명
          </dd>
        </div>
        <div>
          <dt>실패한 요청</dt>
          <dd>{logs.failures}건</dd>
        </div>
        <div>
          <dt>수행업무</dt>
          <dd>{counts(logs.by_action, ACTION_LABELS)}</dd>
        </div>
        <div>
          <dt>데이터 유형</dt>
          <dd>{counts(logs.by_category, DATA_CATEGORY_LABELS)}</dd>
        </div>
        {path === "DB" && (
          <div>
            <dt>정보주체 미특정</dt>
            <dd>
              {logs.subject_unresolved}건{" "}
              <span className="muted">(원문 SQL은 게이트웨이 원문 저장소에 보관)</span>
            </dd>
          </div>
        )}
      </dl>

      <h3 className="report-h3">이상행위 탐지·소명</h3>
      <dl className="summary-grid">
        <div>
          <dt>탐지건</dt>
          <dd>{detections.total}건</dd>
        </div>
        <div>
          <dt>심각도</dt>
          <dd>{counts(detections.by_severity, SEVERITY_LABELS)}</dd>
        </div>
        <div>
          <dt>처리 상태</dt>
          <dd>{counts(detections.by_status, STATUS_LABELS)}</dd>
        </div>
        <div>
          <dt>에스컬레이션</dt>
          <dd>{detections.escalated}건</dd>
        </div>
        <div>
          <dt>소명 요청 / 제출</dt>
          <dd>
            {explanations.requested}건 / {explanations.submitted}건
          </dd>
        </div>
        <div>
          <dt>소명 승인 / 반려</dt>
          <dd>
            {explanations.approved}건 / {explanations.rejected}건
          </dd>
        </div>
        {explanations.overdue !== undefined && (
          <div>
            <dt>소명 기한 초과</dt>
            <dd>{explanations.overdue}건</dd>
          </div>
        )}
      </dl>
      {detections.by_rule.length > 0 && (
        <p className="muted report-note">룰별: {detections.by_rule.map((r) => `${r.name} ${r.count}`).join(" · ")}</p>
      )}

      <h3 className="report-h3">탐지건 목록 (심각도 순, 최대 50건)</h3>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="num">번호</th>
              <th>룰</th>
              <th>심각도</th>
              <th>취급자</th>
              <th>최초 발생</th>
              <th className="num">기록</th>
              <th>정보주체 (마스킹)</th>
              <th>상태 (보고서 생성 시점)</th>
            </tr>
          </thead>
          <tbody>
            {section.cases.map((c) => (
              <Fragment key={c.id}>
              <tr className={hasHandling(c) ? "case-row has-handling" : "case-row"}>
                <td className="num">#{c.id}</td>
                <td>{c.rule_name}</td>
                <td>
                  <span className={severityBadgeClass(c.severity)}>{SEVERITY_LABELS[c.severity]}</span>
                </td>
                <td>
                  {c.actor_name ?? "-"} <span className="muted">({c.actor_login_id})</span>
                </td>
                <td>{formatDateTime(c.first_occurred_at)}</td>
                <td className="num">{c.log_count}</td>
                <td className="subjects">
                  {subjectsLabel(c.subjects, c.distinct_subject_count, c.subject_count_sum)}
                </td>
                <td>
                  <span className={statusBadgeClass(c.status)}>{STATUS_LABELS[c.status]}</span>{" "}
                  {c.round > 0 && <span className="muted">{c.round}차</span>}
                </td>
              </tr>
              {hasHandling(c) && (
                <tr className="case-handling">
                  <td />
                  <td colSpan={7}>
                    <Handling c={c} />
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
            {section.cases.length === 0 && (
              <tr>
                <td colSpan={8} className="muted" style={{ textAlign: "center", padding: 16 }}>
                  기간 중 탐지건이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// 점검 보고서 화면 (기능 레이어 9) — 생성 시점 스냅샷을 그대로 그린다. 인쇄 / PDF로 저장해 보고한다.
// 열람도 Argus 접속기록(READ, 보고서 번호)으로 남는다
export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const { me, handleError } = useMe();
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Report>(`/reports/${id}`)
      .then(setReport)
      .catch((e) => setError(handleError(e)));
  }, [id, handleError]);

  const summary = report?.summary;
  const paths = summary ? (Object.keys(summary.paths) as AccessPath[]) : [];

  return (
    <>
      <div className="no-print">
        <AppHeader me={me} />
      </div>
      <main className="container report">
        <div className="title-row no-print">
          <Link href="/reports">← 보고서 목록</Link>
          <button className="btn btn-primary" onClick={() => window.print()} disabled={!report}>
            인쇄 / PDF로 저장
          </button>
        </div>
        {error && <div className="alert-error">{error}</div>}
        {report && summary && (
          <>
            {summary.integrity.previous?.status === "MISMATCH" && (
              <div className="alert-error">
                직전 보고서 #{summary.integrity.previous.report_id}의 마지막 기록 #
                {summary.integrity.previous.last_id}이(가) 원장에 없거나 바뀌었습니다. 접속기록 삭제·변조
                여부를 확인하세요.
              </div>
            )}
            <h1 className="page-title">개인정보 접속기록 점검 보고서 #{report.id}</h1>
            <dl className="summary-grid report-meta">
              <div>
                <dt>점검 기간 (KST)</dt>
                <dd>{periodLabel(report.period_from, report.period_to)}</dd>
              </div>
              <div>
                <dt>범위</dt>
                <dd>{SCOPE_LABELS[report.scope]}</dd>
              </div>
              <div>
                <dt>작성자</dt>
                <dd>{report.generated_by}</dd>
              </div>
              <div>
                <dt>생성 시각</dt>
                <dd>{formatDateTime(report.generated_at)}</dd>
              </div>
            </dl>
            <p className="muted report-note">
              근거: 개인정보의 안전성 확보조치 기준 §8②(접속기록 월 1회 이상 점검)·§8③(위·변조 방지). 정보주체
              식별값은 마스킹 표시이며(원본 회원번호 미포함), 수치는 보고서를 만든 시점의 기록입니다.
            </p>

            <section className="card">
              <h2 className="card-title">점검 수행 확인</h2>
              <dl className="summary-grid">
                <div>
                  <dt>접속기록 무결성 (해시체인)</dt>
                  <dd>
                    {summary.integrity.ok ? (
                      <span className="badge badge-accent">정상</span>
                    ) : (
                      <span className="badge badge-danger">이상 — 기록 #{summary.integrity.broken_at}</span>
                    )}{" "}
                    <span className="muted">원장 {summary.integrity.checked}건 재계산</span>
                  </dd>
                </div>
                {summary.integrity.last_id !== undefined && (
                  <div>
                    <dt>원장 기준점 (보고서 생성 시점)</dt>
                    <dd>
                      전체 {summary.integrity.total}건 · 마지막 기록 #{summary.integrity.last_id ?? "-"}
                      {summary.integrity.last_hash && (
                        <div className="mono hash">{summary.integrity.last_hash}</div>
                      )}
                    </dd>
                  </div>
                )}
                {summary.integrity.previous && (
                  <div>
                    <dt>직전 보고서 기준점 대조</dt>
                    <dd>
                      <AnchorCheck previous={summary.integrity.previous} />
                    </dd>
                  </div>
                )}
                <div>
                  <dt>자동 점검(탐지 배치)</dt>
                  <dd>
                    {summary.patrol.runs}회 실행 · 실패 {summary.patrol.failed}회
                  </dd>
                </div>
                <div>
                  <dt>마지막 정상 점검</dt>
                  <dd>{formatDateTime(summary.patrol.last_success_at)}</dd>
                </div>
              </dl>
              {paths.length > 1 && (
                <div className="table-wrap" style={{ marginTop: 12 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>경로</th>
                        <th className="num">접속기록</th>
                        <th className="num">탐지건</th>
                        <th className="num">에스컬레이션</th>
                        <th className="num">소명 반려</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paths.map((path) => {
                        const s = summary.paths[path]!;
                        return (
                          <tr key={path}>
                            <td>{PATH_LABELS[path]}</td>
                            <td className="num">{s.logs.total}</td>
                            <td className="num">{s.detections.total}</td>
                            <td className="num">{s.detections.escalated}</td>
                            <td className="num">{s.explanations.rejected}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {paths.map((path) => (
              <Section key={path} path={path} section={summary.paths[path]!} />
            ))}
          </>
        )}
      </main>
    </>
  );
}
