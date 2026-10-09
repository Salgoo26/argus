"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import {
  ApiError,
  adminLoginPath,
  api,
  errorMessage,
  formatDateTime,
  type Operator,
} from "@/lib/api";

// 계정·권한 관리 (v0.1 보강 L-2 — 고시 §5①③) — 관리자(ADMIN) 전용. 서버도 403으로 막는다
// 모든 변경은 사유와 함께 권한 이력에 남고, 본인 계정은 바꿀 수 없다

type Account = {
  id: number;
  login_id: string;
  name: string;
  team: string;
  role: string;
  employment_status: "ACTIVE" | "TERMINATED";
  terminated_at: string | null;
  must_change_password: boolean;
  last_changed_at: string | null;
};

type HistoryItem = {
  id: number;
  login_id: string;
  name: string;
  change_type: "GRANT" | "CHANGE" | "REVOKE";
  before_role: string | null;
  after_role: string;
  before_team: string | null;
  after_team: string;
  before_status: string | null;
  after_status: string;
  reason: string;
  actor_login_id: string | null;
  created_at: string;
};

const ROLES: Record<string, string> = { ADMIN: "관리자", OPS: "운영", CS: "상담", MARKETING: "마케팅" };
const TEAMS: Record<string, string> = { OPS: "운영팀", CS: "CS팀", MARKETING: "마케팅팀" };
const STATUSES: Record<string, string> = { ACTIVE: "재직", TERMINATED: "퇴직" };
const CHANGE_TYPES: Record<string, string> = { GRANT: "부여", CHANGE: "변경", REVOKE: "말소" };

function transition(before: string | null, after: string, labels: Record<string, string>): string {
  if (before === null) return labels[after] ?? after;
  if (before === after) return labels[after] ?? after;
  return `${labels[before] ?? before} → ${labels[after] ?? after}`;
}

function NewAccount({ onCreated, onError }: { onCreated: () => void; onError: (e: unknown) => void }) {
  const [issued, setIssued] = useState<{ login_id: string; password: string } | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    setPending(true);
    try {
      const created = await api<Account & { temporary_password: string }>("/admin/accounts", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(form)),
      });
      // 임시 비밀번호는 이 화면에서 한 번만 — 상태에만 두고 브라우저 저장소에 쓰지 않는다
      setIssued({ login_id: created.login_id, password: created.temporary_password });
      formEl.reset();
      onCreated();
    } catch (e) {
      onError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="card">
      <h2 className="card-title">계정 만들기</h2>
      {issued && (
        <div className="reveal">
          {issued.login_id} 임시 비밀번호: <span className="mono">{issued.password}</span>
          <div className="hint">다시 볼 수 없습니다. 본인에게 전달하세요. 첫 로그인 때 바꿔야 합니다.</div>
        </div>
      )}
      <form className="toolbar" onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="n_login_id">아이디</label>
          <input id="n_login_id" name="login_id" required pattern="[a-z][a-z0-9_]{2,63}" maxLength={64} />
        </div>
        <div className="field">
          <label htmlFor="n_name">이름</label>
          <input id="n_name" name="name" required maxLength={50} />
        </div>
        <div className="field">
          <label htmlFor="n_team">팀</label>
          <select id="n_team" name="team" defaultValue="OPS">
            {Object.entries(TEAMS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="n_role">역할</label>
          <select id="n_role" name="role" defaultValue="OPS">
            {Object.entries(ROLES).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="n_reason">사유</label>
          <input id="n_reason" name="reason" required maxLength={500} />
        </div>
        <button className="btn btn-primary" type="submit" disabled={pending}>
          만들기
        </button>
      </form>
    </section>
  );
}

function AccountRow({
  account,
  isMe,
  onChanged,
  onError,
}: {
  account: Account;
  isMe: boolean;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [role, setRole] = useState(account.role);
  const [team, setTeam] = useState(account.team);
  const [reason, setReason] = useState("");
  const active = account.employment_status === "ACTIVE";

  async function post(path: string, body: object) {
    try {
      await api(`/admin/accounts/${account.id}/${path}`, { method: "POST", body: JSON.stringify(body) });
      setReason("");
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  function terminate() {
    if (!window.confirm(`${account.login_id} 계정을 퇴직 처리합니다. 되돌릴 수 없습니다. 계속할까요?`)) return;
    post("terminate", { reason });
  }

  return (
    <tr>
      <td>{account.login_id}</td>
      <td>{account.name}</td>
      <td>
        {active && !isMe ? (
          <select value={team} onChange={(e) => setTeam(e.target.value)}>
            {Object.entries(TEAMS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        ) : (
          (TEAMS[account.team] ?? account.team)
        )}
      </td>
      <td>
        {active && !isMe ? (
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {Object.entries(ROLES).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        ) : (
          (ROLES[account.role] ?? account.role)
        )}
      </td>
      <td>
        <span className={active ? "badge badge-accent" : "badge"}>{STATUSES[account.employment_status]}</span>
        {account.must_change_password && <div className="hint">임시 비밀번호</div>}
      </td>
      <td>{account.last_changed_at ? formatDateTime(account.last_changed_at) : "-"}</td>
      <td>
        {isMe ? (
          <span className="muted">본인</span>
        ) : active ? (
          <div className="toolbar">
            <input
              aria-label="사유"
              placeholder="사유"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
            />
            <button
              className="btn btn-secondary"
              disabled={!reason.trim()}
              onClick={() => post("change", { role, team, reason })}
            >
              변경
            </button>
            <button className="btn btn-danger" disabled={!reason.trim()} onClick={terminate}>
              퇴직 처리
            </button>
          </div>
        ) : (
          "-"
        )}
      </td>
    </tr>
  );
}

function HistoryTab({ onError }: { onError: (e: unknown) => void }) {
  const [criteria, setCriteria] = useState<Record<string, string>>({});
  const [items, setItems] = useState<HistoryItem[] | null>(null);

  useEffect(() => {
    // 대상 아이디는 URL이 아니라 본문으로
    api<{ items: HistoryItem[] }>("/admin/accounts/history/search", {
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
              <th>팀</th>
              <th>재직</th>
              <th>사유</th>
              <th>처리자</th>
            </tr>
          </thead>
          <tbody>
            {items?.map((h) => (
              <tr key={h.id}>
                <td>{formatDateTime(h.created_at)}</td>
                <td>
                  {h.name} ({h.login_id})
                </td>
                <td>{CHANGE_TYPES[h.change_type]}</td>
                <td>{transition(h.before_role, h.after_role, ROLES)}</td>
                <td>{transition(h.before_team, h.after_team, TEAMS)}</td>
                <td>{transition(h.before_status, h.after_status, STATUSES)}</td>
                <td>{h.reason}</td>
                <td>{h.actor_login_id ?? "시스템"}</td>
              </tr>
            ))}
            {items && items.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">
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

export default function AccountsPage() {
  const router = useRouter();
  const [me, setMe] = useState<Operator | null>(null);
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [tab, setTab] = useState<"accounts" | "history">("accounts");
  const [error, setError] = useState<string | null>(null);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) router.replace(adminLoginPath());
      else setError(errorMessage(e));
    },
    [router],
  );

  const load = useCallback(() => {
    api<{ items: Account[] }>("/admin/accounts")
      .then((r) => setAccounts(r.items))
      .catch(handleError);
  }, [handleError]);

  useEffect(() => {
    api<Operator>("/admin/auth/me").then(setMe).catch(handleError);
    load();
  }, [handleError, load]);

  return (
    <>
      <AppHeader me={me} />
      <main className="container">
        <h1 className="page-title">계정·권한</h1>
        {error && <div className="alert-error">{error}</div>}
        <div className="tabs">
          <button
            className={tab === "accounts" ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab("accounts")}
          >
            계정
          </button>
          <button
            className={tab === "history" ? "btn btn-primary" : "btn btn-secondary"}
            onClick={() => setTab("history")}
          >
            권한 이력
          </button>
        </div>
        {tab === "accounts" ? (
          <>
            <NewAccount onCreated={load} onError={handleError} />
            <section className="card">
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>아이디</th>
                      <th>이름</th>
                      <th>팀</th>
                      <th>역할</th>
                      <th>재직</th>
                      <th>최근 변경</th>
                      <th>변경·말소</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts?.map((a) => (
                      <AccountRow
                        key={`${a.id}-${a.last_changed_at}`}
                        account={a}
                        isMe={a.login_id === me?.login_id}
                        onChanged={load}
                        onError={handleError}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : (
          <HistoryTab onError={handleError} />
        )}
      </main>
    </>
  );
}
