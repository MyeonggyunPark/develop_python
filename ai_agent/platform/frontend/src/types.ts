export type SummaryCard = {
  label: string;
  value: number | string;
  delta?: string;
};

export type Agent = {
  agent_id: string;
  agent_slug: string;
  agent_name: string;
  description: string;
  status: string;
  is_enabled: boolean;
  last_run_at: string;
  run_count_7d: number;
  failed_count_7d: number;
  cost_30d: number;
};

export type Run = {
  run_id: string;
  agent_slug: string;
  agent_name: string;
  status: string;
  trigger_type: string;
  started_at: string;
  ended_at: string | null;
  total_duration_ms: number;
  total_cost: number;
  error_stage: string | null;
  error_type: string | null;
  error_message: string | null;
};

export type Artifact = {
  artifact_type: string;
  artifact_name: string;
  file_url: string;
  version: number;
};

export type RunLog = {
  stage: string;
  status: string;
  message: string;
  duration_ms: number;
};

export type CostRow = {
  agent_slug: string;
  provider: string;
  service_type: string;
  usage_amount: number;
  usage_unit: string;
  cost_amount: number;
  recorded_at: string;
  run_id: string;
};

export type DashboardTrendPoint = {
  label: string;
  runs: number;
  failed: number;
  runs_height: number;
  failed_height: number;
};

export type ProviderChartRow = {
  provider: string;
  amount: number;
  width: number;
};

export type DashboardSummaryResponse = {
  summary_cards: SummaryCard[];
  recent_runs: Run[];
  failed_runs: Run[];
  approval_queue: Run[];
  cost_by_agent: Record<string, number>;
  provider_chart: ProviderChartRow[];
  trend_points: DashboardTrendPoint[];
  usage_cards: Record<string, string>;
  activity_stats: {
    total_runs: number;
    run_requests: number;
    avg_cost: string;
    pending_review: number;
  };
};

export type AgentsResponse = {
  agents: Agent[];
};

export type AgentDetailResponse = {
  agent: Agent;
  runs: Run[];
  latest_run: Run | null;
  cost_total: number;
  artifacts: Artifact[];
};

export type RunDetailResponse = {
  run: Run;
  logs: RunLog[];
  artifacts: Artifact[];
  costs: CostRow[];
};

export type CostsSummaryResponse = {
  summary_cards: SummaryCard[];
  rows: CostRow[];
  total_cost: number;
  by_provider: Record<string, number>;
  by_agent: Record<string, number>;
};

export type SettingsResponse = {
  settings: Record<string, string>;
};

export type HealthResponse = {
  status: string;
};
