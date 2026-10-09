"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { SEVERITY_LABELS, formatDateTime } from "@/lib/labels";
import { severityBadgeClass } from "@/lib/rules";

// 화면 알림 (v0.1 보강 F-2) — 본문 없음: 종류·탐지건 번호·룰 이름·심각도만 (개인정보 없음)
type NotificationItem = {
  id: number;
  kind: "DETECTED" | "REQUESTED" | "DUE_SOON" | "OVERDUE" | "SUBMITTED";
  detection_id: number;
  severity: "HIGH" | "MEDIUM" | "LOW";
  round: number;
  created_at: string;
  read_at: string | null;
  rule_name: string;
};
type NotificationPage = { items: NotificationItem[]; unread: number };

const POLL_MS = 30_000; // 접속 중에는 30초마다 새 알림 확인 【기본값】
const KIND_LABELS: Record<NotificationItem["kind"], string> = {
  DETECTED: "새 탐지건",
  REQUESTED: "소명 요청",
  DUE_SOON: "소명 기한 임박",
  OVERDUE: "소명 기한 초과",
  SUBMITTED: "소명 제출",
};
// 심각도를 색만으로 구분하지 않도록 기호도 함께
const SEVERITY_ICONS = { HIGH: "▲", MEDIUM: "■", LOW: "●" } as const;

export function NotificationBell() {
  const router = useRouter();
  const [page, setPage] = useState<NotificationPage | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api<NotificationPage>("/notifications")
      .then(setPage)
      .catch(() => undefined); // 확인 실패는 다음 주기에 다시 — 화면을 막지 않는다
  }, []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(load, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  // 바깥을 누르면 닫는다
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function go(item: NotificationItem) {
    setOpen(false);
    if (!item.read_at) await api(`/notifications/${item.id}/read`, { method: "POST" }).catch(() => undefined);
    load();
    router.push(`/detections/${item.detection_id}`);
  }

  async function readAll() {
    await api("/notifications/read-all", { method: "POST" }).catch(() => undefined);
    load();
  }

  const unread = page?.unread ?? 0;
  return (
    <div className="notify" ref={box}>
      <button
        className="btn btn-secondary notify-button"
        onClick={() => setOpen((o) => !o)}
        aria-label={`알림 ${unread}건 안 읽음`}
        aria-expanded={open}
      >
        알림
        {unread > 0 && <span className="notify-count">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="notify-panel" role="dialog" aria-label="알림 목록">
          <div className="notify-head">
            <strong>알림</strong>
            <button className="link-button" onClick={readAll} disabled={unread === 0}>
              모두 읽음
            </button>
          </div>
          {page && page.items.length === 0 && <p className="muted notify-empty">알림이 없습니다.</p>}
          <ul className="notify-list">
            {page?.items.map((item) => (
              <li key={item.id}>
                <button className={item.read_at ? "notify-item" : "notify-item unread"} onClick={() => go(item)}>
                  <span className={severityBadgeClass(item.severity)}>
                    {SEVERITY_ICONS[item.severity]} {SEVERITY_LABELS[item.severity]}
                  </span>
                  <span className="notify-text">
                    <strong>{KIND_LABELS[item.kind]}</strong> · #{item.detection_id} {item.rule_name}
                    {item.round > 0 && ` (${item.round}차)`}
                  </span>
                  <span className="muted notify-time">{formatDateTime(item.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
