import type {
  AgentDetailResponse,
  AgentsResponse,
  AgentWorkspaceResponse,
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

async function postJson<T>(path: string, body?: Record<string, string>): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
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

export function fetchAgentWorkspace(agentSlug: string) {
  return fetchJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace`);
}

export function fetchAgentRuns(agentSlug: string) {
  return fetchJson<{ runs: AgentDetailResponse["runs"] }>(`/api/agents/${agentSlug}/runs`);
}

export function startAgentWorkspace(agentSlug: string) {
  return postJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace/start`);
}

export function requestAgentRecommendations(agentSlug: string) {
  return postJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace/request-recommendations`);
}

export function submitAgentTopic(agentSlug: string, topic: string) {
  return postJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace/submit-topic`, { topic });
}

export function selectAgentTopic(agentSlug: string, topic: string) {
  return postJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace/select-topic`, { topic });
}

export function regenerateAgentScript(agentSlug: string) {
  return postJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace/regenerate-script`);
}

export function finalizeAgentRun(agentSlug: string) {
  return postJson<AgentWorkspaceResponse>(`/api/agents/${agentSlug}/workspace/finalize`);
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
