// 룰 빌더 화면용 — 조건식 JSON을 "필드별 입력칸"으로 보여 주고, 저장할 때 다시 조건식으로 바꾼다.
// 형식의 최종 판단은 언제나 서버(validate_rule)가 한다. 화면은 담당자가 틀리기 어렵게 돕는 역할.

import { ACTION_LABELS, DATA_CATEGORY_LABELS } from "@/lib/labels";

export type RuleType = "EVENT" | "AGGREGATE";
export type Severity = "HIGH" | "MEDIUM" | "LOW";
export type Leaf = { field: string; op: string; value: unknown };
export type Condition = { all: Leaf[] } | { any: Leaf[] };
export type AggregateSpec = {
  window: "1h" | "1d" | "1mo";
  measure: "LOG_COUNT" | "SUBJECT_COUNT" | "DISTINCT_SUBJECT";
  compare: "ABSOLUTE" | "RATIO_TO_BASELINE";
  threshold: number;
  baseline?: "PREV_MONTH_SAME_PERIOD";
  min_baseline?: number;
};

export type Rule = {
  id: number;
  name: string;
  description: string | null;
  rule_type: RuleType;
  access_path: "APP" | "DB" | "ALL";
  severity: Severity;
  enabled: boolean;
  auto_request: boolean;
  condition: Condition;
  aggregate: AggregateSpec | null;
  version: number;
};

export type RuleSummary = Rule & {
  updated_at: string;
  updated_by: string | null; // null = 시스템(마이그레이션 시드)
  detection_count: number;
};

export type RuleHistory = {
  version: number;
  change_type: "CREATE" | "UPDATE" | "ENABLE" | "DISABLE";
  snapshot: Rule;
  changed_at: string;
  changed_by: string | null;
};

export type RuleDetail = RuleSummary & { history: RuleHistory[] };

// ── 라벨 ─────────────────────────────────────────────

export const RULE_TYPE_LABELS: Record<RuleType, string> = { EVENT: "단건", AGGREGATE: "집계" };
// policy 1-5 표기 — 탐지건·접속기록 화면(lib/labels.ts PATH_LABELS)과 같은 말
export const ACCESS_PATH_LABELS = { APP: "화면 경유(3티어)", DB: "DB 직접(2티어)", ALL: "전체" } as const;
export const WINDOW_LABELS = { "1h": "1시간(매시 정각부터)", "1d": "하루(0시부터)", "1mo": "한 달(1일부터)" };
export const MEASURE_LABELS = {
  LOG_COUNT: "기록 수",
  SUBJECT_COUNT: "처리 건수 합",
  DISTINCT_SUBJECT: "고유 정보주체 수",
};
export const CHANGE_LABELS = { CREATE: "생성", UPDATE: "수정", ENABLE: "켜기", DISABLE: "끄기" };
export const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
export const WEEKDAY_LABELS: Record<string, string> = {
  MON: "월",
  TUE: "화",
  WED: "수",
  THU: "목",
  FRI: "금",
  SAT: "토",
  SUN: "일",
};
export const RULE_ACTIONS = ["LOGIN", "LOGOUT", "READ", "CREATE", "UPDATE", "DELETE", "DOWNLOAD"];
export const RULE_CATEGORIES = ["MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY"];

// 조건 필드 — 서버 app/detection/rules.py의 FIELDS와 같은 목록
export const FIELDS = [
  { key: "action", label: "수행업무" },
  { key: "data_category", label: "데이터 유형" },
  { key: "result", label: "결과" },
  { key: "subject_count", label: "처리 건수" },
  { key: "occurred_time", label: "시각(한국)" },
  { key: "occurred_weekday", label: "요일(한국)" },
  { key: "actor_terminated_at_or_before", label: "퇴직 여부" },
] as const;
export type FieldKey = (typeof FIELDS)[number]["key"];

export const COUNT_OPS = { gte: "이상", lte: "이하", eq: "같음" } as const;

/** 필드를 처음 고를 때의 기본 조건 */
export function defaultLeaf(field: FieldKey): Leaf {
  switch (field) {
    case "action":
      return { field, op: "eq", value: "READ" };
    case "data_category":
      return { field, op: "eq", value: "MEMBER_BASIC" };
    case "result":
      return { field, op: "eq", value: "FAILURE" };
    case "subject_count":
      return { field, op: "gte", value: 50 };
    case "occurred_time":
      return { field, op: "between", value: ["22:00", "06:00"] };
    case "occurred_weekday":
      return { field, op: "in", value: ["SAT", "SUN"] };
    case "actor_terminated_at_or_before":
      return { field, op: "eq", value: true };
  }
}

/** 수행업무·데이터 유형은 체크박스로 고르고, 하나면 eq·여럿이면 in으로 저장한다 */
export function choicesOf(leaf: Leaf): string[] {
  return Array.isArray(leaf.value) ? (leaf.value as string[]) : [String(leaf.value)];
}

export function withChoices(leaf: Leaf, choices: string[]): Leaf {
  return choices.length === 1
    ? { field: leaf.field, op: "eq", value: choices[0] }
    : { field: leaf.field, op: "in", value: choices };
}

/** 조건식이 화면에서 편집 가능한 모양(한 묶음, 중첩 없음)인가 */
export function isFlat(condition: Condition): boolean {
  const items = "all" in condition ? condition.all : condition.any;
  return items.every((item) => "field" in item);
}

export function leavesOf(condition: Condition): { joiner: "all" | "any"; leaves: Leaf[] } {
  return "all" in condition
    ? { joiner: "all", leaves: condition.all }
    : { joiner: "any", leaves: condition.any };
}

// ── 요약 문구 (목록·이력) ─────────────────────────────

function describeLeaf(leaf: Leaf): string {
  const choices = (labels: Record<string, string>) =>
    choicesOf(leaf)
      .map((v) => labels[v] ?? v)
      .join("·");
  switch (leaf.field) {
    case "action":
      return `수행업무 ${choices(ACTION_LABELS)}`;
    case "data_category":
      return `데이터 ${choices(DATA_CATEGORY_LABELS)}`;
    case "result":
      return leaf.value === "FAILURE" ? "결과 실패" : "결과 성공";
    case "subject_count":
      return `처리 건수 ${leaf.value}건 ${COUNT_OPS[leaf.op as keyof typeof COUNT_OPS] ?? leaf.op}`;
    case "occurred_time": {
      const [start, end] = leaf.value as string[];
      return `${start}~${end}`;
    }
    case "occurred_weekday":
      return `${choices(WEEKDAY_LABELS)}요일`;
    case "actor_terminated_at_or_before":
      return "행위 시점에 퇴직한 계정";
    default:
      return `${leaf.field} ${leaf.op} ${JSON.stringify(leaf.value)}`;
  }
}

export function describeRule(rule: Pick<Rule, "condition" | "aggregate">): string {
  const { joiner, leaves } = leavesOf(rule.condition);
  const condition = isFlat(rule.condition)
    ? leaves.map(describeLeaf).join(joiner === "all" ? " 그리고 " : " 또는 ")
    : "(중첩 조건)";
  const spec = rule.aggregate;
  if (!spec) return condition;
  const window = { "1h": "1시간", "1d": "하루", "1mo": "한 달" }[spec.window];
  const measure = MEASURE_LABELS[spec.measure];
  const criterion =
    spec.compare === "ABSOLUTE"
      ? `${measure} ${spec.threshold} 이상`
      : `${measure}이 전월 같은 기간의 ${spec.threshold}배 이상`;
  return `${condition} → ${window} ${criterion}`;
}

export function severityBadgeClass(severity: string): string {
  if (severity === "HIGH") return "badge badge-danger";
  if (severity === "MEDIUM") return "badge badge-warn";
  return "badge";
}
