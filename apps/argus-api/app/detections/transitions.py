"""탐지건 상태 전이 — 허용된 길만 열어 둔 결재 흐름 (policy 3, actor-flows F-05·F-06)

표에 없는 (현재 상태, 행동) 조합은 모두 거부한다(409). 역할이 맞지 않으면 403.

    DETECTED ──요청──▶ REQUESTED ──제출──▶ SUBMITTED ──승인──▶ APPROVED (종결)
       │                  │                     └────반려──▶ REJECTED ──재요청(차수+1)──▶ REQUESTED
       └──불요──▶ DISMISSED ◀──요청 취소──┘
                                              REJECTED ──에스컬레이션──▶ ESCALATED (종결)

- 자동 소명 요청(2026-10-01 사용자 확정): 탐지 배치가 DETECTED → REQUESTED를 시스템으로 수행.
  담당자는 목록을 보고 오탐이면 **요청 취소**(REQUESTED → DISMISSED, 사유 필수)
- 취급자의 "오탐 취소 요청" 경로는 두지 않는다(같은 날 확정 — 흐름 복잡, 오탐 판단 기준 애매)
- 에스컬레이션은 정보보안팀 이관 플래그까지 — 이관 이후는 스코프 밖(policy 3-2)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Transition:
    to_status: str
    role: str  # 이 행동을 할 수 있는 역할
    round_step: int = 0  # 요청 차수 증가량 (재요청 +1)
    closes: bool = False  # 종결 상태로 가는가 (closed_at 기록)


OFFICER, HANDLER = "OFFICER", "HANDLER"

# (행동, 현재 상태) → 전이
TRANSITIONS: dict[tuple[str, str], Transition] = {
    ("request", "DETECTED"): Transition("REQUESTED", OFFICER, round_step=1),  # 수동 요청
    ("request", "REJECTED"): Transition("REQUESTED", OFFICER, round_step=1),  # 재요청
    ("dismiss", "DETECTED"): Transition("DISMISSED", OFFICER, closes=True),  # 소명 불요
    ("dismiss", "REQUESTED"): Transition("DISMISSED", OFFICER, closes=True),  # 요청 취소(오탐)
    ("submit", "REQUESTED"): Transition("SUBMITTED", HANDLER),
    ("approve", "SUBMITTED"): Transition("APPROVED", OFFICER, closes=True),
    ("reject", "SUBMITTED"): Transition("REJECTED", OFFICER),
    ("escalate", "REJECTED"): Transition("ESCALATED", OFFICER, closes=True),
}

ACTIONS = frozenset(action for action, _ in TRANSITIONS)
ACTION_ROLES = {action: t.role for (action, _), t in TRANSITIONS.items()}
