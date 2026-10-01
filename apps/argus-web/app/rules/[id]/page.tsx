"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { type RuleBody, RuleForm } from "@/components/rule-form";
import { ApiError, api } from "@/lib/api";
import { SEVERITY_LABELS, formatDateTime } from "@/lib/labels";
import {
  CHANGE_LABELS,
  RULE_TYPE_LABELS,
  type RuleDetail,
  describeRule,
  severityBadgeClass,
} from "@/lib/rules";

export default function RuleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me, handleError } = useMe();
  const [rule, setRule] = useState<RuleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const isOfficer = me?.role === "OFFICER";

  const load = useCallback(() => {
    api<RuleDetail>(`/rules/${id}`)
      .then(setRule)
      .catch((e) => setError(handleError(e)));
  }, [id, handleError]);

  useEffect(() => {
    if (isOfficer) load();
  }, [isOfficer, load]);

  // 화면이 본 버전을 함께 보낸다 — 그사이 다른 담당자가 고쳤으면 서버가 409로 거부
  async function send(path: string, method: string, body: object, done: string) {
    if (!rule) return;
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      await api(path, { method, body: JSON.stringify({ ...body, expected_version: rule.version }) });
      setNotice(done);
      load();
    } catch (e) {
      const detail = e instanceof ApiError && e.code === "INVALID_RULE" ? ` (${e.message})` : "";
      setError(`${handleError(e) ?? ""}${detail}`);
    } finally {
      setPending(false);
    }
  }

  const save = (body: RuleBody) =>
    send(`/rules/${id}`, "PUT", body, "저장했습니다. 다음 순찰부터 적용됩니다.");

  function toggle() {
    if (!rule) return;
    const action = rule.enabled ? "disable" : "enable";
    void send(`/rules/${id}/${action}`, "POST", {}, rule.enabled ? "룰을 껐습니다." : "룰을 켰습니다.");
  }

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <p>
          <Link href="/rules">← 룰 목록</Link>
        </p>
        {me && !isOfficer && <div className="alert-error">정보보호 담당자 전용 화면입니다.</div>}
        {error && <div className="alert-error">{error}</div>}
        {notice && <div className="notice-ok">{notice}</div>}

        {rule && (
          <>
            <div className="title-row">
              <h1 className="page-title">
                {rule.name}{" "}
                <span className={rule.enabled ? "badge badge-accent" : "badge"}>
                  {rule.enabled ? "켜짐" : "꺼짐"}
                </span>
              </h1>
              <button
                className={rule.enabled ? "btn btn-danger" : "btn btn-primary"}
                disabled={pending}
                onClick={toggle}
              >
                {rule.enabled ? "끄기" : "켜기"}
              </button>
            </div>
            <p className="page-subtitle">
              {RULE_TYPE_LABELS[rule.rule_type]} ·{" "}
              <span className={severityBadgeClass(rule.severity)}>{SEVERITY_LABELS[rule.severity]}</span>{" "}
              · v{rule.version} · 이 룰로 생긴 탐지건 {rule.detection_count}건
            </p>

            <section className="card">
              <h2 className="card-title">수정</h2>
              {/* 버전이 바뀌면 폼을 새로 그려 서버의 최신 값으로 시작한다 */}
              <RuleForm key={rule.version} initial={rule} submitLabel="저장" pending={pending} onSubmit={save} />
            </section>

            <section className="card">
              <h2 className="card-title">변경 이력</h2>
              <ul className="timeline">
                {rule.history.map((h) => (
                  <li key={h.version}>
                    <time>{formatDateTime(h.changed_at)}</time>
                    <span>
                      <strong>
                        v{h.version} {CHANGE_LABELS[h.change_type]}
                      </strong>{" "}
                      · {h.changed_by ?? "시스템"}
                      <br />
                      <span className="muted">
                        {h.snapshot.name} — {describeRule(h.snapshot)} · 심각도{" "}
                        {SEVERITY_LABELS[h.snapshot.severity]} · {h.snapshot.enabled ? "켜짐" : "꺼짐"}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </main>
    </>
  );
}
