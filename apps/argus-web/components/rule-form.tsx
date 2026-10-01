"use client";

import { type FormEvent, useState } from "react";

import { ACTION_LABELS, DATA_CATEGORY_LABELS } from "@/lib/labels";
import {
  ACCESS_PATH_LABELS,
  type AggregateSpec,
  COUNT_OPS,
  type FieldKey,
  FIELDS,
  type Leaf,
  MEASURE_LABELS,
  RULE_ACTIONS,
  RULE_CATEGORIES,
  RULE_TYPE_LABELS,
  type Rule,
  type RuleType,
  type Severity,
  WEEKDAYS,
  WEEKDAY_LABELS,
  WINDOW_LABELS,
  choicesOf,
  defaultLeaf,
  isFlat,
  leavesOf,
  withChoices,
} from "@/lib/rules";

export type RuleBody = {
  name: string;
  description: string | null;
  rule_type?: RuleType;
  severity: Severity;
  access_path: Rule["access_path"];
  auto_request: boolean;
  condition: Rule["condition"];
  aggregate: AggregateSpec | null;
};

const DEFAULT_AGGREGATE: AggregateSpec = {
  window: "1h",
  measure: "LOG_COUNT",
  compare: "ABSOLUTE",
  threshold: 100,
};

/** 체크박스 묶음 — 하나 이상 골라야 한다 */
function Checks({
  options,
  labels,
  selected,
  onChange,
}: {
  options: string[];
  labels: Record<string, string>;
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <span className="checks">
      {options.map((option) => (
        <label key={option}>
          <input
            type="checkbox"
            checked={selected.includes(option)}
            onChange={(e) => {
              const next = e.target.checked
                ? options.filter((o) => o === option || selected.includes(o))
                : selected.filter((o) => o !== option);
              if (next.length > 0) onChange(next);
            }}
          />
          {labels[option] ?? option}
        </label>
      ))}
    </span>
  );
}

/** 필드마다 맞는 입력칸 — 연산자는 담당자에게 드러내지 않거나 말로 보여 준다 */
function LeafEditor({ leaf, onChange }: { leaf: Leaf; onChange: (leaf: Leaf) => void }) {
  switch (leaf.field as FieldKey) {
    case "action":
      return (
        <Checks
          options={RULE_ACTIONS}
          labels={ACTION_LABELS}
          selected={choicesOf(leaf)}
          onChange={(next) => onChange(withChoices(leaf, next))}
        />
      );
    case "data_category":
      return (
        <Checks
          options={RULE_CATEGORIES}
          labels={DATA_CATEGORY_LABELS}
          selected={choicesOf(leaf)}
          onChange={(next) => onChange(withChoices(leaf, next))}
        />
      );
    case "result":
      return (
        <select value={String(leaf.value)} onChange={(e) => onChange({ ...leaf, value: e.target.value })}>
          <option value="FAILURE">실패</option>
          <option value="SUCCESS">성공</option>
        </select>
      );
    case "subject_count":
      return (
        <span className="inline">
          <input
            type="number"
            min={0}
            value={Number(leaf.value)}
            onChange={(e) => onChange({ ...leaf, value: Number(e.target.value) })}
            style={{ width: 100 }}
          />
          건
          <select value={leaf.op} onChange={(e) => onChange({ ...leaf, op: e.target.value })}>
            {Object.entries(COUNT_OPS).map(([op, label]) => (
              <option key={op} value={op}>
                {label}
              </option>
            ))}
          </select>
        </span>
      );
    case "occurred_time": {
      const [start, end] = leaf.value as string[];
      return (
        <span className="inline">
          <input type="time" value={start} onChange={(e) => onChange({ ...leaf, value: [e.target.value, end] })} />
          부터
          <input type="time" value={end} onChange={(e) => onChange({ ...leaf, value: [start, e.target.value] })} />
          전까지 <span className="muted">(끝이 시작보다 이르면 자정을 넘김)</span>
        </span>
      );
    }
    case "occurred_weekday":
      return (
        <Checks
          options={WEEKDAYS}
          labels={WEEKDAY_LABELS}
          selected={choicesOf(leaf)}
          onChange={(next) => onChange({ ...leaf, value: next })}
        />
      );
    case "actor_terminated_at_or_before":
      return <span className="muted">행위 시점에 이미 퇴직한 계정 (취급자 명부에 없는 계정 포함)</span>;
    default:
      return <code>{JSON.stringify(leaf.value)}</code>;
  }
}

export function RuleForm({
  initial,
  submitLabel,
  pending,
  onSubmit,
}: {
  initial: Rule | null; // null = 새 룰
  submitLabel: string;
  pending: boolean;
  onSubmit: (body: RuleBody) => void;
}) {
  const creating = initial === null;
  const start = initial ? leavesOf(initial.condition) : { joiner: "all" as const, leaves: [defaultLeaf("action")] };
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [ruleType, setRuleType] = useState<RuleType>(initial?.rule_type ?? "EVENT");
  const [severity, setSeverity] = useState<Severity>(initial?.severity ?? "MEDIUM");
  const [accessPath, setAccessPath] = useState<Rule["access_path"]>(initial?.access_path ?? "APP");
  const [autoRequest, setAutoRequest] = useState(initial?.auto_request ?? true);
  const [joiner, setJoiner] = useState<"all" | "any">(start.joiner);
  const [leaves, setLeaves] = useState<Leaf[]>(start.leaves);
  const [aggregate, setAggregate] = useState<AggregateSpec>(initial?.aggregate ?? DEFAULT_AGGREGATE);

  if (initial && !isFlat(initial.condition)) {
    // 엔진은 한 단계 중첩을 허용하지만 화면은 한 묶음만 다룬다 (화면으로 만든 룰은 모두 한 묶음)
    return <div className="alert-error">중첩된 조건식은 화면에서 편집할 수 없습니다.</div>;
  }

  const setLeaf = (index: number, leaf: Leaf) =>
    setLeaves((all) => all.map((l, i) => (i === index ? leaf : l)));
  const ratio = aggregate.compare === "RATIO_TO_BASELINE";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let spec: AggregateSpec | null = null;
    if (ruleType === "AGGREGATE") {
      spec = ratio
        ? {
            window: "1mo", // 전월 같은 기간 비교는 월 단위에서만
            measure: aggregate.measure,
            compare: "RATIO_TO_BASELINE",
            baseline: "PREV_MONTH_SAME_PERIOD",
            threshold: aggregate.threshold,
            ...(aggregate.min_baseline ? { min_baseline: aggregate.min_baseline } : {}),
          }
        : {
            window: aggregate.window,
            measure: aggregate.measure,
            compare: "ABSOLUTE",
            threshold: Math.round(aggregate.threshold),
          };
    }
    onSubmit({
      name: name.trim(),
      description: description.trim() || null,
      ...(creating ? { rule_type: ruleType } : {}),
      severity,
      access_path: accessPath,
      auto_request: autoRequest,
      condition: (joiner === "all" ? { all: leaves } : { any: leaves }) as Rule["condition"],
      aggregate: spec,
    });
  }

  return (
    <form className="rule-form" onSubmit={submit}>
      <div className="toolbar">
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label htmlFor="name">룰 이름</label>
          <input id="name" value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="rule_type">유형</label>
          {creating ? (
            <select id="rule_type" value={ruleType} onChange={(e) => setRuleType(e.target.value as RuleType)}>
              <option value="EVENT">단건 — 기록 1건으로 판정</option>
              <option value="AGGREGATE">집계 — 구간 안의 기록을 세어 판정</option>
            </select>
          ) : (
            <span style={{ padding: "8px 0" }}>{RULE_TYPE_LABELS[ruleType]} (만든 뒤엔 바꿀 수 없음)</span>
          )}
        </div>
        <div className="field">
          <label htmlFor="severity">심각도</label>
          <select id="severity" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
            <option value="HIGH">상</option>
            <option value="MEDIUM">중</option>
            <option value="LOW">하</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="access_path">접근 경로</label>
          <select
            id="access_path"
            value={accessPath}
            onChange={(e) => setAccessPath(e.target.value as Rule["access_path"])}
          >
            {Object.entries(ACCESS_PATH_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field">
        <label htmlFor="description">설명 (룰 취지)</label>
        <textarea
          id="description"
          value={description}
          maxLength={1000}
          style={{ minHeight: 60 }}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <h3 className="card-title">조건</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        아래 조건을{" "}
        <select value={joiner} onChange={(e) => setJoiner(e.target.value as "all" | "any")}>
          <option value="all">모두 만족</option>
          <option value="any">하나라도 만족</option>
        </select>{" "}
        하는 접속기록이 대상입니다. 시각·요일은 한국 시각 기준입니다.
      </p>
      {leaves.map((leaf, index) => (
        <div className="rule-row" key={index}>
          <select
            value={leaf.field}
            onChange={(e) => setLeaf(index, defaultLeaf(e.target.value as FieldKey))}
          >
            {FIELDS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
          <LeafEditor leaf={leaf} onChange={(next) => setLeaf(index, next)} />
          {leaves.length > 1 && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setLeaves((all) => all.filter((_, i) => i !== index))}
            >
              삭제
            </button>
          )}
        </div>
      ))}
      <button
        type="button"
        className="btn btn-secondary"
        onClick={() => setLeaves((all) => [...all, defaultLeaf("result")])}
      >
        + 조건 추가
      </button>

      {ruleType === "AGGREGATE" && (
        <>
          <h3 className="card-title" style={{ marginTop: 20 }}>
            집계
          </h3>
          <div className="toolbar">
            <div className="field">
              <label htmlFor="compare">기준</label>
              <select
                id="compare"
                value={aggregate.compare}
                onChange={(e) => {
                  const compare = e.target.value as AggregateSpec["compare"];
                  setAggregate((a) =>
                    compare === "RATIO_TO_BASELINE"
                      ? { ...a, compare, window: "1mo", threshold: 2, min_baseline: a.min_baseline ?? 20 }
                      : { ...a, compare, threshold: 100 },
                  );
                }}
              >
                <option value="ABSOLUTE">정해진 값 이상</option>
                <option value="RATIO_TO_BASELINE">전월 같은 기간 대비 배율 이상</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="window">구간</label>
              <select
                id="window"
                value={aggregate.window}
                disabled={ratio}
                onChange={(e) => setAggregate((a) => ({ ...a, window: e.target.value as AggregateSpec["window"] }))}
              >
                {Object.entries(WINDOW_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="measure">무엇을 셀지</label>
              <select
                id="measure"
                value={aggregate.measure}
                onChange={(e) =>
                  setAggregate((a) => ({ ...a, measure: e.target.value as AggregateSpec["measure"] }))
                }
              >
                {Object.entries(MEASURE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="threshold">{ratio ? "배율" : "기준값"}</label>
              <input
                id="threshold"
                type="number"
                min={ratio ? 0.1 : 1}
                step={ratio ? 0.1 : 1}
                value={aggregate.threshold}
                required
                style={{ width: 110 }}
                onChange={(e) => setAggregate((a) => ({ ...a, threshold: Number(e.target.value) }))}
              />
            </div>
            {ratio && (
              <div className="field">
                <label htmlFor="min_baseline">기준선 최소 건수</label>
                <input
                  id="min_baseline"
                  type="number"
                  min={1}
                  value={aggregate.min_baseline ?? ""}
                  style={{ width: 110 }}
                  onChange={(e) =>
                    setAggregate((a) => ({ ...a, min_baseline: Number(e.target.value) || undefined }))
                  }
                />
              </div>
            )}
          </div>
          {ratio && (
            <p className="muted" style={{ fontSize: 12 }}>
              전월 같은 기간의 값이 기준선 최소 건수보다 작으면 비율을 믿을 수 없어 판정하지 않습니다.
            </p>
          )}
        </>
      )}

      <label className="inline" style={{ marginTop: 16 }}>
        <input type="checkbox" checked={autoRequest} onChange={(e) => setAutoRequest(e.target.checked)} />
        탐지 즉시 해당 취급자에게 소명을 자동 요청
      </label>

      <p className="muted" style={{ fontSize: 12 }}>
        저장한 룰은 다음 탐지 순찰부터 적용되며, 이미 지나간 기록을 다시 판정하지 않습니다.
      </p>
      <button className="btn btn-primary" type="submit" disabled={pending}>
        {submitLabel}
      </button>
    </form>
  );
}
