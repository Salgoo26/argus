"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { AttachmentList, AttachmentUploader } from "@/components/attachments";
import { DbStatement, SubjectsCell } from "@/components/db-statement";
import { DueLabel } from "@/components/due-label";
import { TicketInput } from "@/components/ticket-input";
import { api, type CaseDetail } from "@/lib/api";
import {
  ACTION_LABELS,
  type ActionButton,
  EXPLANATION_GUIDE,
  PATH_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  actionsFor,
  caseNature,
  formatDateTime,
  pathBadgeClass,
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
  const [tickets, setTickets] = useState<string[]>([]);
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
    // 제출 뒤에 붙은 기록은 이 소명이 다루지 않았다 — 승인은 막지 않고 확인만 (v0.1 보강 J-1)
    const late = detail?.after_submission_count ?? 0;
    if (
      button.action === "approve" &&
      late > 0 &&
      !window.confirm(
        `제출 뒤 추가 기록 ${late}건은 이 소명에 포함되지 않습니다. 그래도 승인하시겠습니까?\n(재요청하려면 취소한 뒤 반려하세요.)`,
      )
    ) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api(`/detections/${id}/${button.action}`, {
        method: "POST",
        body: JSON.stringify({
          ...(value ? { [button.field]: value } : {}),
          // 제출할 때만 관련 티켓을 함께 보낸다 (소명 내용과 별도 칸)
          ...(button.action === "submit" ? { ticket_ids: tickets } : {}),
        }),
      });
      setText("");
      setTickets([]);
      load();
    } catch (e) {
      setError(handleError(e));
    } finally {
      setPending(false);
    }
  }

  // 본인 건은 열람만 — 처리는 다른 담당자가 (v0.1 보강 H, 서버도 403으로 막는다)
  const ownCase = !!detail?.own_case;
  const buttons = me && detail && !ownCase ? actionsFor(me.role, detail.status) : [];
  const needsText = buttons.length > 0;
  const isAggregate = detail?.rule.rule_type === "AGGREGATE";
  // 지금 차수의 소명 — 취급자가 요청 중일 때 첨부를 올리고 지울 수 있다
  const currentRound = detail?.explanations.find((e) => e.round === detail.round);
  const canAttach = me?.role === "HANDLER" && detail?.status === "REQUESTED" && !!currentRound;
  // 경로별 소명 기준 (policy 3-3) — 취급자에겐 작성 안내, 담당자에겐 검토 기준
  const guide = detail ? EXPLANATION_GUIDE[detail.access_path] : null;
  const showGuide =
    !!guide &&
    ((me?.role === "HANDLER" && detail?.status === "REQUESTED") ||
      (me?.role === "OFFICER" && detail?.status === "SUBMITTED"));

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
              <span className={statusBadgeClass(detail.status)}>{STATUS_LABELS[detail.status]}</span>{" "}
              <span className={pathBadgeClass(detail.access_path)}>{PATH_LABELS[detail.access_path]}</span>
            </h1>
            <p className="page-subtitle">{detail.rule.description}</p>

            <section className="card">
              <dl className="summary-grid">
                <div>
                  <dt>접근 경로</dt>
                  <dd>{PATH_LABELS[detail.access_path]}</dd>
                </div>
                {caseNature(detail) && (
                  <div>
                    <dt>처리 구분</dt>
                    <dd>{caseNature(detail)}</dd>
                  </div>
                )}
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
                {detail.after_submission_count > 0 && (
                  <div>
                    <dt>소명 제출 뒤 추가 기록</dt>
                    <dd>
                      <span className="badge badge-warn">{detail.after_submission_count}건</span>
                    </dd>
                  </div>
                )}
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

            {ownCase && (
              <section className="card">
                <p className="muted" style={{ margin: 0 }}>
                  본인 건은 다른 담당자가 처리합니다.
                </p>
              </section>
            )}

            {needsText && (
              <section className="card">
                <h2 className="card-title">
                  {me?.role === "HANDLER" ? "소명 작성" : "처리"}
                </h2>
                {showGuide && guide && (
                  <div className="guide">
                    <strong>
                      {PATH_LABELS[detail.access_path]}{" "}
                      {me?.role === "HANDLER" ? "소명에 적을 것" : "검토 기준"}
                    </strong>
                    <ul>
                      {(me?.role === "HANDLER" ? guide.write : guide.review).map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {me?.role === "HANDLER" && (
                  // 소명은 자유 입력이라 점검 보고서에 요지가 그대로 실린다 (v0.1 보강 B)
                  <p className="privacy-note">
                    고객 이름·연락처 등 개인정보는 적지 말고 회원번호·주문번호로 적어 주세요. 소명
                    요지는 점검 보고서에 실립니다.
                  </p>
                )}
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={me?.role === "HANDLER" ? 5000 : 1000}
                  placeholder={
                    me?.role === "HANDLER"
                      ? "위 안내에 따라 행위 사유를 구체적으로 작성하세요."
                      : "요청 메시지 또는 사유·의견 (불요·요청 취소·반려는 필수)"
                  }
                />
                {canAttach && <TicketInput value={tickets} onChange={setTickets} />}
                {canAttach && detail && currentRound && (
                  <AttachmentUploader
                    detectionId={detail.id}
                    attachments={currentRound.attachments}
                    onChanged={load}
                    onError={setError}
                  />
                )}
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
                      <th>{detail.access_path === "DB" ? "실행한 SQL (정규화)" : "기능"}</th>
                      <th className="num">처리 건수</th>
                      <th>정보주체</th>
                      <th>연계 티켓</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.logs.map((log) => (
                      <tr
                        key={log.access_log_id}
                        className={log.after_submission_round ? "after-submission" : undefined}
                      >
                        <td>
                          {formatDateTime(log.occurred_at)}
                          {log.after_submission_round && (
                            <div className="small">{log.after_submission_round}차 소명 제출 뒤</div>
                          )}
                        </td>
                        <td>{ACTION_LABELS[log.action] ?? log.action}</td>
                        <td>
                          <span className={log.result === "SUCCESS" ? "badge" : "badge badge-danger"}>
                            {log.result === "SUCCESS" ? "성공" : "실패"}
                          </span>
                        </td>
                        <td>{log.client_ip}</td>
                        <td>
                          {log.db ? (
                            <DbStatement db={log.db} />
                          ) : (
                            <>
                              {log.request_method} {log.request_path}
                            </>
                          )}
                        </td>
                        <td className="num">{log.subject_count}</td>
                        <td className="subjects">
                          <SubjectsCell
                            subjects={log.subjects.slice(0, SUBJECT_PREVIEW)}
                            count={log.subject_count}
                            db={log.db}
                          />
                        </td>
                        <td>{log.ticket_id ?? "-"}</td>
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
                    {e.due_at && (
                      <>
                        {" · "}
                        <DueLabel due={e.due_at} submitted={e.submitted_at} />
                      </>
                    )}
                  </p>
                  {e.request_message && <div className="quote">{e.request_message}</div>}
                  {e.submitted_at ? (
                    <>
                      <p className="muted" style={{ margin: 0 }}>
                        제출 {formatDateTime(e.submitted_at)} · {e.submitted_by}
                      </p>
                      {/* 취급자가 쓴 내용은 텍스트로만 표시 — HTML로 해석하지 않는다(XSS 방지) */}
                      <div className="quote">{e.content}</div>
                      <AttachmentList
                        detectionId={detail.id}
                        attachments={e.attachments}
                        onError={setError}
                      />
                      {e.tickets.length > 0 && (
                        <ul className="tickets">
                          {e.tickets.map((t) => (
                            <li key={t.ticket_id}>
                              <span className="mono">{t.ticket_id}</span>{" "}
                              {/* 내용은 플랫폼에서 — 새 탭, 원래 창을 조작할 수 없게(noopener) */}
                              <a href={t.url} target="_blank" rel="noopener noreferrer">
                                플랫폼에서 보기 ↗
                              </a>
                            </li>
                          ))}
                        </ul>
                      )}
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
