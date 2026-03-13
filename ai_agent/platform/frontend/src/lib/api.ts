import type {
  AgentDetailResponse,
  AgentsResponse,
  CostsSummaryResponse,
  DashboardSummaryResponse,
  HealthResponse,
  RunDetailResponse,
  SettingsResponse,
} from "../types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }

  return (await response.json()) as T;
}

export function fetchHealth() {
  return fetchJson<HealthResponse>("/health");
}

export function fetchDashboard() {
  return fetchJson<DashboardSummaryResponse>("/api/dashboard/summary");
}

export function fetchAgents() {
  return fetchJson<AgentsResponse>("/api/agents");
}

export function fetchAgentDetail(agentSlug: string) {
  return fetchJson<AgentDetailResponse>(`/api/agents/${agentSlug}`);
}

export function fetchAgentRuns(agentSlug: string) {
  return fetchJson<{ runs: AgentDetailResponse["runs"] }>(`/api/agents/${agentSlug}/runs`);
}

export function fetchRunDetail(runId: string) {
  return fetchJson<RunDetailResponse>(`/api/runs/${runId}`);
}

export function fetchCosts() {
  return fetchJson<CostsSummaryResponse>("/api/costs/summary");
}

export function fetchSettings() {
  return fetchJson<SettingsResponse>("/api/settings");
}
