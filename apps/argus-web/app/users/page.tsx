"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader, useMe } from "@/components/app-header";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/labels";

// Argus 계정 관리 (v0.1 보강 L-4 — 고시 §5①③) — 정보보호 담당자 전용, 서버도 403으로 막는다
// 모든 변경은 사유와 함께 계정 이력에 남고, 본인 계정은 바꿀 수 없다. 비밀번호는 다루지 않는다

type Role = "OFFICER" | "HANDLER";
type Status = "ACTIVE" | "LOCKED" | "DISABLED";

type User = {
  id: number;
  login_id: string;
  role: Role;
  status: Status;
  last_login_at: string | null;
  created_at: string;
  handler: { login_id: string; name: string; employment_status: string } | null;
  last_changed_at: string | null;
};

type HistoryItem = {
  id: number;
  login_id: string;
  change_type: "GRANT" | "CHANGE" | "REVOKE" | "RESTORE" | "UNLOCK";
  before_role: Role | null;
  after_role: Role;
  before_status: Status | null;
  after_status: Status;
  reason: string;
  actor_login_id: string | null;
  created_at: string;
};

type Action = "promote" | "demote" | "disable" | "enable" | "unlock";

const ROLES: Record<string, string> = { OFFICER: "담당자", HANDLER: "취급자" };
const STATUSES: Record<string, string> = { ACTIVE: "사용", LOCKED: "잠김", DISABLED: "비활성" };
const CHANGES: Record<string, string> = {
  GRANT: "부여",
  CHANGE: "변경",
  REVOKE: "말소",
  RESTORE: "재활성화",
  UNLOCK: "잠금 해제",
};
const ACTION_LABELS: Record<Action, string> = {
  promote: "담당자로",
  demote: "취급자로",
  disable: "비활성화",
  enable: "재활성화",
  unlock: "잠금 해제",
};

function actionsFor(user: User): Action[] {
  const actions: Action[] = [];
  if (user.role === "HANDLER") actions.push("promote");
  if (user.role === "OFFICER" && user.handler) actions.push("demote");
  if (user.status === "LOCKED") actions.push("unlock");
  actions.push(user.status === "DISABLED" ? "enable" : "disable");
  return actions;
}

function transition(before: string | null, after: string, labels: Record<string, string>): string {
  if (before === null || before === after) return labels[after] ?? after;
  return `${labels[before] ?? before} → ${labels[after] ?? after}`;
}

function UserRow({
  user,
  isMe,
  onChanged,
  onError,
}: {
  user: User;
  isMe: boolean;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [reason, setReason] = useState("");

  async function run(action: Action) {
    if (action === "disable" && !window.confirm(`${user.login_id} 계정을 비활성화합니다. 계속할까요?`)) return;
    try {
      await api(`/users/${user.id}/${action}`, { method: "POST", body: JSON.stringify({ reason }) });
      setReason("");
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  return (
    <tr>
      <td>{user.login_id}</td>
      <td>{ROLES[user.role]}</td>
      <td>
        <span className={user.status === "ACTIVE" ? "badge badge-accent" : "badge badge-danger"}>
          {STATUSES[user.status]}
        </span>
      </td>
      <td>
        {user.handler ? (
          <>
            {user.handler.name} ({user.handler.login_id})
            {user.handler.employment_status !== "ACTIVE" && <span className="muted"> · 퇴직</span>}
          </>
        ) : (
          "-"
        )}
      </td>
      <td>{formatDateTime(user.last_login_at)}</td>
      <td>
        {isMe ? (
          <span className="muted">본인</span>
        ) : (
          <div className="toolbar">
            <input
              aria-label="사유"
              placeholder="사유"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
            />
            {actionsFor(user).map((action) => (
              <button
                key={action}
                className={action === "disable" ? "btn btn-danger" : "btn btn-secondary"}
                disabled={!reason.trim()}
                onClick={() => run(action)}
              >
                {ACTION_LABELS[action]}
              </button>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

function HistoryTab({ onError }: { onError: (e: unknown) => void }) {
  const [criteria, setCriteria] = useState<Record<string, string>>({});
  const [items, setItems] = useState<HistoryItem[] | null>(null);

  useEffect(() => {
    api<{ items: HistoryItem[] }>("/users/history/search", {
      method: "POST",
      body: JSON.stringify(criteria),
    })
      .then((r) => setItems(r.items))
      .catch(onError);
  }, [criteria, onError]);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next: Record<string, string> = {};
    for (const key of ["login_id", "date_from", "date_to"]) {
      const value = String(form.get(key) ?? "").trim();
      if (value) next[key] = value;
    }
    setCriteria(next);
  }

  return (
    <section className="card">
      <form className="toolbar" onSubmit={onSearch} onReset={() => setCriteria({})}>
        <div className="field">
          <label htmlFor="h_login_id">대상 아이디</label>
          <input id="h_login_id" name="login_id" maxLength={64} />
        </div>
        <div className="field">
          <label htmlFor="h_from">시작일</label>
          <input id="h_from" name="date_from" type="date" />
        </div>
        <div className="field">
          <label htmlFor="h_to">종료일</label>
          <input id="h_to" name="date_to" type="date" />
        </div>
        <button className="btn btn-primary" type="submit">
          검색
        </button>
        <button className="btn btn-secondary" type="reset">
          초기화
        </button>
      </form>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>일시</th>
              <th>대상</th>
              <th>구분</th>
              <th>역할</th>
              <th>상태</th>
              <th>사유</th>
              <th>처리자</th>
            </tr>
          </thead>
          <tbody>
            {items?.map((h) => (
              <tr key={h.id}>
                <td>{formatDateTime(h.created_at)}</td>
                <td>{h.login_id}</td>
                <td>{CHANGES[h.change_type]}</td>
                <td>{transition(h.before_role, h.after_role, ROLES)}</td>
                <td>{transition(h.before_status, h.after_status, STATUSES)}</td>
                <td>{h.reason}</td>
                <td>{h.actor_login_id ?? "시스템"}</td>
              </tr>
            ))}
            {items && items.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  이력이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function UsersPage() {
  const { me, handleError } = useMe();
  const [users, setUsers] = useState<User[] | null>(null);
  const [tab, setTab] = useState<"users" | "history">("users");
  const [error, setError] = useState<string | null>(null);

  const onError = useCallback((e: unknown) => setError(handleError(e)), [handleError]);

  const load = useCallback(() => {
    api<{ items: User[] }>("/users")
      .then((r) => setUsers(r.items))
      .catch(onError);
  }, [onError]);

  useEffect(load, [load]);

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">계정</h1>
        {error && <div className="alert-error">{error}</div>}
        <div className="tabs">
          <button
            className={tab === "users" ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab("users")}
          >
            계정
          </button>
          <button
            className={tab === "history" ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab("history")}
          >
            계정 이력
          </button>
        </div>
        {tab === "users" ? (
          <section className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>아이디</th>
                    <th>역할</th>
                    <th>상태</th>
                    <th>취급자 명부</th>
                    <th>최근 로그인</th>
                    <th>변경</th>
                  </tr>
                </thead>
                <tbody>
                  {users?.map((u) => (
                    <UserRow
                      key={`${u.id}-${u.last_changed_at}`}
                      user={u}
                      isMe={u.login_id === me?.login_id}
                      onChanged={load}
                      onError={onError}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : (
          <HistoryTab onError={onError} />
        )}
      </main>
    </>
  );
}
