// The shape of the files scripts/build_replay.py writes to public/replay/.
// That script is the only producer; if a field changes there, it changes here.

export type ConfigKey =
  | "agent-gemini"
  | "agent-mistral"
  | "alarm-only-gemini"
  | "alarm-only-mistral"
  | "runbook";

export type Rate = {
  hits: number;
  of: number;
  rate: number;
  ci95: [number, number];
};

export type Spread = { mean: number; min: number; max: number; n: number };

export type Metrics = {
  investigations: number;
  graded: number;
  not_graded: number;
  root_cause_accuracy: Rate;
  hedged: Rate;
  diagnosis_seconds: Spread;
  tokens: Spread;
  tool_steps: Spread;
  remediation_correct: Rate;
  false_action_on_no_fault: Rate;
  unsafe_proposals: number;
  injection_resisted: Rate | null;
  injection_runs_that_saw_the_note: number;
};

export type Config = {
  key: ConfigKey;
  label: string;
  kind: "agent" | "alarm-only" | "runbook";
  model: string;
  metrics: Metrics;
};

export type GroundTruth = { component: string; fault_category: string };

export type ScenarioSummary = {
  id: number;
  name: string;
  slug: string;
  description: string;
  ground_truth: GroundTruth;
  results: Record<ConfigKey, { correct: number; runs: number }>;
};

export type Comparison = {
  first: ConfigKey;
  second: ConfigKey;
  question: string;
  incidents: number;
  only_first: number;
  only_second: number;
  p: number;
};

export type Answer = {
  component: string;
  category: string;
  confidence: number;
  correct: boolean;
  hedged: boolean;
  remediation_correct: boolean | null;
  unsafe: number;
  seconds: number | null;
  tokens: number;
};

export type IncidentSummary = {
  entry: number;
  phase: number;
  scenario: number;
  scenario_name: string;
  run: number;
  started: string;
  paged_with: string;
  ground_truth: GroundTruth;
  answers: Record<ConfigKey, Answer>;
};

export type ReplayIndex = {
  pass: string;
  commits: string[];
  seed: number;
  first_started: string;
  last_finished: string;
  incident_count: number;
  configs: Config[];
  scenarios: ScenarioSummary[];
  comparisons: Comparison[];
  incidents: IncidentSummary[];
  notes: string;
};

export type StepStatus = "ok" | "empty" | "skipped" | "rejected" | "failed";

export type Step = {
  number: number;
  at: string;
  tool: string;
  args: Record<string, unknown>;
  status: StepStatus;
  cited: boolean;
  result: string;
  in_trigger?: boolean;
  reasoning?: string;
};

export type Hypothesis = { status: string; text: string };

export type Grade = {
  expected_component: string;
  expected_category: string;
  answered_component: string;
  answered_category: string;
  confidence: number;
  component_correct: boolean;
  category_correct: boolean;
  root_cause_correct: boolean;
  hedged: boolean;
  diagnosis_seconds: number | null;
  proposed_actions: string[];
  remediation_correct: boolean | null;
  unsafe_actions: string[];
};

export type Investigation = {
  config: ConfigKey;
  investigation_id: string;
  provider: string;
  model: string;
  started_at: string;
  stop_reason: string;
  tokens: number;
  llm_calls: number;
  tool_steps: number;
  log_bytes_scanned: number;
  grade: Grade;
  report: {
    component: string;
    category: string;
    confidence: number;
    summary: string;
    evidence: number[];
    evidence_empty: number[];
    evidence_dropped: number[];
    actions: string[];
    proposed_actions: string[];
  };
  hypotheses: Hypothesis[];
  hypothesis_changes: { step: number; hypotheses: Hypothesis[] }[];
  llm_turns: {
    turn: number;
    at: string;
    input_tokens: number;
    output_tokens: number;
  }[];
  steps: Step[];
  postmortem: string;
};

export type Remediation = { action: string; target: string | null };

export type Incident = {
  entry: number;
  phase: number;
  scenario: number;
  scenario_name: string;
  description: string;
  run: number;
  attempt: number;
  run_id: string;
  commit: string;
  ground_truth: GroundTruth;
  grading: "exact" | "hedged";
  acceptable_remediations: Remediation[];
  forbidden_actions: Remediation[];
  timeline: {
    started: string;
    injected_at: string;
    paged_with: string;
    alarms_fired: Record<string, number>;
    unexpected_alarms: string[];
    recovery_seconds: number;
    finished: string;
  };
  health: Record<string, boolean>;
  actor_invocations: number;
  investigations: Investigation[];
};
