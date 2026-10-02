"use client";

import { type KeyboardEvent, useState } from "react";

const MAX_TICKETS = 3;
const TICKET = /^INQ-[1-9][0-9]{0,9}$/;

// 관련 업무 티켓 입력 — 소명 내용과 별도 칸 (2026-10-02 사용자 결정)
// 취급자가 플랫폼 1:1 문의 번호(INQ-12)를 직접 적는다. 서버도 같은 형식을 다시 검사하고,
// 번호가 맞는지는 담당자가 링크로 플랫폼 문의를 열어 확인한다(Argus는 문의 내용을 갖지 않음)
export function TicketInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function add() {
    const ticket = draft.trim().toUpperCase();
    if (!ticket) return;
    if (!TICKET.test(ticket)) return setProblem("INQ-숫자 형식으로 입력하세요 (예: INQ-12).");
    if (value.includes(ticket)) return setProblem("이미 추가한 티켓입니다.");
    onChange([...value, ticket]);
    setDraft("");
    setProblem(null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  }

  return (
    <div className="ticket-input">
      <label htmlFor="ticket">관련 업무 티켓 (선택)</label>
      <div className="ticket-row">
        {value.map((t) => (
          <span key={t} className="badge ticket-chip">
            {t}
            <button
              type="button"
              aria-label={`${t} 빼기`}
              onClick={() => onChange(value.filter((v) => v !== t))}
            >
              ×
            </button>
          </span>
        ))}
        {value.length < MAX_TICKETS && (
          <>
            <input
              id="ticket"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="INQ-12"
              maxLength={14}
            />
            <button type="button" className="btn btn-secondary btn-small" onClick={add}>
              추가
            </button>
          </>
        )}
      </div>
      {problem && <p className="field-error">{problem}</p>}
      <p className="muted hint">
        이 행위의 근거가 된 플랫폼 1:1 문의 번호(최대 {MAX_TICKETS}개). 담당자가 번호로 플랫폼 문의를
        열어 확인합니다. 제출하면 바꿀 수 없습니다.
      </p>
    </div>
  );
}
