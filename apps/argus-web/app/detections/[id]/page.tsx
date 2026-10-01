"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api, type CaseDetail } from "@/lib/api";
import {
  ACTION_LABELS,
  type ActionButton,
  SEVERITY_LABELS,
  STATUS_LABELS,
  actionsFor,
  formatDateTime,
  statusBadgeClass,
} from "@/lib/labels";

const SUBJECT_PREVIEW = 5;

// 집계값 표시 — 절대 기준이면 건수, 전월 대비면 배율 (기준은 탐지 당시 룰 사본에서)
function aggregateLabel(detail: CaseDetail): string {
  const spec = detail.rule.aggregate;
  if (detail.aggregate_value === null || !spec) return "-";
  if (spec.compare === "RATIO_TO_BASELINE") {
    return `전월 같은 기간의 ${detail.aggregate_value.toFixed(2)}배 (기준 ${spec.threshold}배)`;
  }
  return `${detail.aggregate_value}건 (기준 ${spec.threshold}건)`;
}

export default function DetectionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me, handleError } = useMe();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);

  const load = useCallback(() => {
    // 상세 조회는 Argus 자체 접속기록(READ, 보여준 마스킹 식별값 수)으로 남는다
    api<CaseDetail>(`/detections/${id}`)
      .then(setDetail)
      .catch((e) => setError(handleError(e)));
  }, [id, handleError]);

  useEffect(load, [load]);

  async function run(button: ActionButton) {
    const value = text.trim();
    if (button.required && !value) {
      setError(`${button.label}: 내용을 입력하세요.`);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api(`/detections/${id}/${button.action}`, {
        method: "POST",
        body: JSON.stringify(value ? { [button.field]: value } : {}),
      });
      setText("");
      load();
    } catch (e) {
      setError(handleError(e));
    } finally {
      setPending(false);
    }
  }

  const buttons = me && detail ? actionsFor(me.role, detail.status) : [];
  const needsText = buttons.length > 0;
  const isAggregate = detail?.rule.rule_type === "AGGREGATE";

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <p>
          <Link href="/detections">← 목록</Link>
        </p>
        {error && <div className="alert-error">{error}</div>}
        {detail && (
          <>
            <h1 className="page-title">
              #{detail.id} {detail.rule.name}{" "}
              <span className={statusBadgeClass(detail.status)}>{STATUS_LABELS[detail.status]}</span>
            </h1>
            <p className="page-subtitle">{detail.rule.description}</p>

            <section className="card">
              <dl className="summary-grid">
                <div>
                  <dt>취급자</dt>
                  <dd>
                    {detail.actor_name ?? "-"} ({detail.actor_login_id})
                  </dd>
                </div>
                <div>
                  {/* EVENT는 발생 날짜, AGGREGATE는 집계 구간(윈도우)의 시작 시각이 그룹 키다 */}
                  <dt>{isAggregate ? "집계 구간 시작 (KST)" : "발생일 (KST)"}</dt>
                  <dd>{detail.group_bucket.replace("T", " ").replace("+09:00", "")}</dd>
                </div>
                {isAggregate && (
                  <div>
                    <dt>집계값</dt>
                    <dd>{aggregateLabel(detail)}</dd>
                  </div>
                )}
                <div>
                  <dt>심각도</dt>
                  <dd>{SEVERITY_LABELS[detail.severity]}</dd>
                </div>
                <div>
                  <dt>기록 / 처리 건수</dt>
                  <dd>
                    {detail.log_count}건 / {detail.subject_count_sum ?? "-"}건
                  </dd>
                </div>
                <div>
                  <dt>고유 정보주체</dt>
                  <dd>
                    {detail.distinct_subject_count ?? "-"}명
                    {detail.log_summary?.subject_ids_truncated && " 이상"}
                  </dd>
                </div>
                <div>
                  <dt>소명 차수</dt>
                  <dd>{detail.round}차</dd>
                </div>
                <div>
                  <dt>탐지 시각</dt>
                  <dd>{formatDateTime(detail.detected_at)}</dd>
                </div>
                {detail.closed_at && (
                  <div>
                    <dt>종결 시각</dt>
                    <dd>{formatDateTime(detail.closed_at)}</dd>
                  </div>
                )}
              </dl>
              {detail.close_reason && (
                <>
                  <p className="muted" style={{ margin: "12px 0 0" }}>
                    종결 사유
                  </p>
                  <div className="quote">{detail.close_reason}</div>
                </>
              )}
            </section>

            {needsText && (
              <section className="card">
                <h2 className="card-title">
                  {me?.role === "HANDLER" ? "소명 작성" : "처리"}
                </h2>
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={me?.role === "HANDLER" ? 5000 : 1000}
                  placeholder={
                    me?.role === "HANDLER"
                      ? "행위 사유를 구체적으로 작성하세요 (예: 요청 부서, 업무 목적, 근거 문서)."
                      : "요청 메시지 또는 사유·의견 (불요·요청 취소·반려는 필수)"
                  }
                />
                <div className="actions">
                  {buttons.map((b) => (
                    <button
                      key={b.action}
                      className={`btn ${b.style}`}
                      disabled={pending}
                      onClick={() => run(b)}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              </section>
            )}

            <section className="card">
              <h2 className="card-title">하위 접속기록</h2>
              <p className="muted" style={{ marginTop: -6 }}>
                정보주체 식별값은 마스킹되어 표시됩니다(서버에서 가려서 전달).
              </p>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>발생 시각</th>
                      <th>행위</th>
                      <th>결과</th>
                      <th>접속지</th>
                      <th>기능</th>
                      <th className="num">처리 건수</th>
                      <th>정보주체</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.logs.map((log) => (
                      <tr key={log.access_log_id}>
                        <td>{formatDateTime(log.occurred_at)}</td>
                        <td>{ACTION_LABELS[log.action] ?? log.action}</td>
                        <td>
                          <span className={log.result === "SUCCESS" ? "badge" : "badge badge-danger"}>
                            {log.result === "SUCCESS" ? "성공" : "실패"}
                          </span>
                        </td>
                        <td>{log.client_ip}</td>
                        <td>
                          {log.request_method} {log.request_path}
                        </td>
                        <td className="num">{log.subject_count}</td>
                        <td className="subjects">
                          {log.subjects.slice(0, SUBJECT_PREVIEW).join(", ")}
                          {log.subjects.length > SUBJECT_PREVIEW &&
                            ` 외 ${log.subject_count - SUBJECT_PREVIEW}명`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="card">
              <h2 className="card-title">소명</h2>
              {detail.explanations.length === 0 && <p className="muted">아직 소명 요청이 없습니다.</p>}
              {detail.explanations.map((e) => (
                <div key={e.round} className="round">
                  <h3>
                    {e.round}차{" "}
                    {e.review_result && (
                      <span className={e.review_result === "APPROVED" ? "badge badge-accent" : "badge badge-danger"}>
                        {e.review_result === "APPROVED" ? "승인" : "반려"}
                      </span>
                    )}
                  </h3>
                  <p className="muted" style={{ margin: 0 }}>
                    요청 {formatDateTime(e.requested_at)} · {e.requested_by ?? "시스템(자동 요청)"}
                  </p>
                  {e.request_message && <div className="quote">{e.request_message}</div>}
                  {e.submitted_at ? (
                    <>
                      <p className="muted" style={{ margin: 0 }}>
                        제출 {formatDateTime(e.submitted_at)} · {e.submitted_by}
                      </p>
                      {/* 취급자가 쓴 내용은 텍스트로만 표시 — HTML로 해석하지 않는다(XSS 방지) */}
                      <div className="quote">{e.content}</div>
                    </>
                  ) : (
                    <p className="muted">제출 대기 중</p>
                  )}
                  {e.reviewed_at && (
                    <>
                      <p className="muted" style={{ margin: 0 }}>
                        검토 {formatDateTime(e.reviewed_at)} · {e.reviewed_by}
                      </p>
                      {e.review_comment && <div className="quote">{e.review_comment}</div>}
                    </>
                  )}
                </div>
              ))}
            </section>

            <section className="card">
              <h2 className="card-title">상태 이력</h2>
              <ol className="timeline">
                {detail.history.map((h, i) => (
                  <li key={i}>
                    <time>{formatDateTime(h.created_at)}</time>
                    <span>
                      {h.from_status ? `${STATUS_LABELS[h.from_status]} → ` : ""}
                      <strong>{STATUS_LABELS[h.to_status]}</strong>
                      <span className="muted"> · {h.actor ?? "시스템"}</span>
                      {h.comment && <span className="muted"> — {h.comment}</span>}
                    </span>
                  </li>
                ))}
              </ol>
            </section>
          </>
        )}
      </main>
    </>
  );
}
