import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import {
  fetchAgentDetail,
  fetchAgentRuns,
  fetchAgents,
  fetchCosts,
  fetchDashboard,
  fetchHealth,
  fetchRunDetail,
  fetchSettings,
} from "./lib/api";
import type {
  Agent,
  AgentDetailResponse,
  Artifact,
  CostRow,
  CostsSummaryResponse,
  DashboardSummaryResponse,
  Run,
  RunDetailResponse,
  SettingsResponse,
} from "./types";

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  idle: "Idle",
  script_review: "In Review",
  approved: "Approved",
  posted: "Posted",
  failed: "Failed",
  rejected: "Rejected",
  disabled: "Disabled",
  done: "Done",
  waiting: "Waiting",
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "Manual",
  scheduled: "Scheduled",
};

type AsyncState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

function useAsyncData<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setState({ data: null, loading: true, error: null });

      try {
        const data = await loader();
        if (!cancelled) {
          setState({ data, loading: false, error: null });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : "알 수 없는 오류",
          });
        }
      }
    }

    run();

    return () => {
      cancelled = true;
    };
  }, deps);

  return state;
}

function formatMoney(value: number) {
  return `$${value.toFixed(2)}`;
}

function formatDuration(totalDurationMs: number) {
  return `${(totalDurationMs / 60000).toFixed(1)} min`;
}

function getStatusLabel(status: string) {
  return STATUS_LABELS[status] ?? status;
}

function getTriggerLabel(trigger: string) {
  return TRIGGER_LABELS[trigger] ?? trigger;
}

function getAgentInitials(agentName: string) {
  return agentName.slice(0, 2);
}

function LoadingCard() {
  return <section className="card">데이터를 불러오는 중입니다.</section>;
}

function ErrorCard({ message }: { message: string }) {
  return <section className="card error-card">데이터를 불러오지 못했습니다. {message}</section>;
}

function HeaderMeta() {
  const location = useLocation();

  if (location.pathname.startsWith("/agents/") && location.pathname.endsWith("/runs")) {
    return {
      title: "Run History",
      description: "에이전트별 실행 결과, 상태, 비용, 소요 시간을 한눈에 확인합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname.startsWith("/agents/")) {
    return {
      title: "Agent Overview",
      description: "등록된 자동화 에이전트를 운영하기 위한 내부 콘솔입니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname.startsWith("/runs/")) {
    return {
      title: "Run Summary",
      description: "등록된 자동화 에이전트를 운영하기 위한 내부 콘솔입니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname === "/agents") {
    return {
      title: "Agents",
      description: "등록된 자동화 에이전트를 운영하기 위한 내부 콘솔입니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname === "/costs") {
    return {
      title: "Costs",
      description: "등록된 자동화 에이전트를 운영하기 위한 내부 콘솔입니다.",
      activeNav: "costs",
    };
  }

  if (location.pathname === "/settings") {
    return {
      title: "Settings",
      description: "등록된 자동화 에이전트를 운영하기 위한 내부 콘솔입니다.",
      activeNav: "settings",
    };
  }

  return {
    title: "Dashboard",
    description: "등록된 자동화 에이전트를 운영하기 위한 내부 콘솔입니다.",
    activeNav: "dashboard",
  };
}

function AppShell() {
  const health = useAsyncData(fetchHealth, []);
  const meta = HeaderMeta();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <div className="brand-mark">AP</div>
            <div>
              <div className="brand-title">Agent Platform</div>
              <div className="brand-subtitle">Multi-Agent Ops Console</div>
            </div>
          </div>
          <div className="nav-section-title">Main</div>
          <nav className="nav">
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} end to="/">
              <span className="nav-icon">◫</span>
              <span>Dashboard</span>
            </NavLink>
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} to="/agents">
              <span className="nav-icon">◇</span>
              <span>Agents</span>
            </NavLink>
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} to="/costs">
              <span className="nav-icon">◌</span>
              <span>Costs</span>
            </NavLink>
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} to="/settings">
              <span className="nav-icon">▣</span>
              <span>Settings</span>
            </NavLink>
          </nav>
        </div>
        <div className="sidebar-footer">
          <Link className="button sidebar-button" to="/agents">
            + New Agent
          </Link>
          <div className="support-card">
            <div className="support-title">Quick Note</div>
            <div className="support-text">
              새 자동화 에이전트는 Agents 화면에서 등록하고 상세 페이지에서 실행합니다.
            </div>
            <div className="support-health">API: {health.data?.status ?? (health.loading ? "checking" : "offline")}</div>
          </div>
        </div>
      </aside>
      <main className="content">
        <header className="page-header">
          <div className="page-title-block">
            <h1>{meta.title}</h1>
            <p className="muted">{meta.description}</p>
          </div>
          <div className="header-actions">
            <label className="search-box">
              <span className="search-icon">⌕</span>
              <input type="text" placeholder="Search agents, run IDs, settings" readOnly />
            </label>
            <span className="icon-button" aria-hidden="true">
              ⟳
            </span>
            <span className="icon-button" aria-hidden="true">
              ◌
            </span>
            <span className="pill">Last 7 days</span>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:agentSlug" element={<AgentDetailPage />} />
          <Route path="/agents/:agentSlug/runs" element={<AgentRunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/costs" element={<CostsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

function DashboardPage() {
  const state = useAsyncData(fetchDashboard, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const data = state.data;

  return (
    <section className="dashboard-grid">
      <div className="dashboard-main">
        <section className="hero-strip">
          <article className="hero-card">
            <div className="hero-card-copy">
              <div className="hero-kicker">Overview</div>
              <h2>에이전트 운영 상황을 한 화면에서 관리합니다.</h2>
              <p>실행 상태, 승인 대기, API 비용을 동시에 확인하고 병목 구간을 빠르게 찾을 수 있습니다.</p>
            </div>
            <div className="hero-card-stats">
              <div className="hero-stat">
                <span>Active</span>
                <strong>{data.summary_cards[0]?.value}</strong>
              </div>
              <div className="hero-stat">
                <span>Failed</span>
                <strong>{data.summary_cards[2]?.value}</strong>
              </div>
            </div>
          </article>
        </section>

        <section className="card-grid">
          {data.summary_cards.map((card, index) => (
            <article className={`card summary-card ${index === 0 ? "summary-card-primary" : ""}`} key={card.label}>
              <div className="summary-icon">{index === 0 ? "◎" : index === 1 ? "▷" : index === 2 ? "△" : "◌"}</div>
              <div>
                <div className="card-label">{card.label}</div>
                <div className="card-value">{card.value}</div>
                <div className="card-delta">{card.delta}</div>
              </div>
            </article>
          ))}
        </section>

        <section className="two-column">
          <article className="card">
            <div className="card-head">
              <h2>Run Trend</h2>
              <span className="subtle-tag">Last 7 days</span>
            </div>
            <div className="chart-legend">
              <span>
                <i className="legend-dot legend-dot-accent" />
                Runs
              </span>
              <span>
                <i className="legend-dot legend-dot-warn" />
                Failed
              </span>
            </div>
            <div className="trend-chart">
              {data.trend_points.map((point) => (
                <div className="trend-item" key={point.label}>
                  <div className="trend-bars">
                    <span className="trend-bar trend-bar-failed" style={{ height: `${point.failed_height}%` }} />
                    <span className="trend-bar trend-bar-runs" style={{ height: `${point.runs_height}%` }} />
                  </div>
                  <div className="trend-label">{point.label}</div>
                </div>
              ))}
            </div>
          </article>
          <article className="card">
            <div className="card-head">
              <h2>Cost Breakdown</h2>
              <span className="subtle-tag">By Provider</span>
            </div>
            <ul className="distribution-list">
              {data.provider_chart.map((row) => (
                <li key={row.provider}>
                  <div className="distribution-head">
                    <span>{row.provider}</span>
                    <strong>{formatMoney(row.amount)}</strong>
                  </div>
                  <div className="distribution-track">
                    <span className="distribution-fill" style={{ width: `${row.width}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </article>
        </section>

        <section className="three-column">
          <article className="card">
            <div className="card-head">
              <h2>Recent Runs</h2>
              <span className="subtle-tag">Live</span>
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Status</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                    </td>
                    <td>
                      <span className="badge">{getStatusLabel(run.status)}</span>
                    </td>
                    <td>{formatMoney(run.total_cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </article>
          <article className="card">
            <div className="card-head">
              <h2>Failed Runs</h2>
              <span className="subtle-tag">Alert</span>
            </div>
            <ul className="stack-list">
              {data.failed_runs.length > 0 ? (
                data.failed_runs.map((run) => (
                  <li key={run.run_id}>
                    <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                    <span className="muted">{run.error_stage ?? "-"}</span>
                  </li>
                ))
              ) : (
                <li className="muted">실패한 실행이 없습니다.</li>
              )}
            </ul>
          </article>
          <article className="card">
            <div className="card-head">
              <h2>Review Queue</h2>
              <span className="subtle-tag">Needs Review</span>
            </div>
            <ul className="stack-list">
              {data.approval_queue.length > 0 ? (
                data.approval_queue.map((run) => (
                  <li key={run.run_id}>
                    <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                    <span className="muted">{getStatusLabel(run.status)}</span>
                  </li>
                ))
              ) : (
                <li className="muted">승인 대기 항목이 없습니다.</li>
              )}
            </ul>
          </article>
        </section>
      </div>

      <aside className="activity-panel">
        <article className="card activity-card">
          <div className="card-head">
            <h2>Activity</h2>
            <span className="subtle-tag">This week</span>
          </div>
          <div className="activity-highlight">
            이번 주 에이전트가 절약한 예상 시간은 <strong>3.5시간</strong>입니다.
          </div>
          <div className="activity-metrics">
            <div className="mini-stat">
              <div className="mini-label">Total Runs</div>
              <div className="mini-value">{data.activity_stats.total_runs}</div>
            </div>
            <div className="mini-stat">
              <div className="mini-label">Run Requests</div>
              <div className="mini-value">{data.activity_stats.run_requests}</div>
            </div>
            <div className="mini-stat">
              <div className="mini-label">Avg Cost</div>
              <div className="mini-value">{data.activity_stats.avg_cost}</div>
            </div>
            <div className="mini-stat">
              <div className="mini-label">Pending Review</div>
              <div className="mini-value">{data.activity_stats.pending_review}</div>
            </div>
          </div>
          <div className="panel-divider" />
          <div className="activity-list">
            <div className="activity-row">
              <span className="activity-dot" />
              <div>
                <strong>웹툰 자동화 에이전트</strong>
                <div className="muted">스크립트 검토 대기 실행 1건이 있습니다.</div>
              </div>
            </div>
            <div className="activity-row">
              <span className="activity-dot activity-dot-warn" />
              <div>
                <strong>Cost Alert</strong>
                <div className="muted">이미지 생성 비용이 지난주 대비 8% 증가했습니다.</div>
              </div>
            </div>
          </div>
          <div className="panel-divider" />
          <div className="activity-usage">
            <div className="card-head compact-head">
              <h3>Usage Snapshot</h3>
              <span className="subtle-tag">API</span>
            </div>
            <ul className="metric-list">
              {Object.entries(data.usage_cards).map(([key, value]) => (
                <li key={key}>
                  <span>{key}</span>
                  <strong>{value}</strong>
                </li>
              ))}
            </ul>
          </div>
        </article>
      </aside>
    </section>
  );
}

function AgentsPage() {
  const state = useAsyncData(fetchAgents, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <section className="section-intro">
        <div>
          <div className="section-kicker">Agent Registry</div>
          <h2>Agents</h2>
          <p className="muted">플랫폼에 연결된 자동화 에이전트를 확인하고 상세 페이지에서 직접 실행할 수 있습니다.</p>
        </div>
        <Link className="button" to="/agents">
          + New Agent
        </Link>
      </section>

      <section className="agent-list">
        {state.data.agents.map((agent) => (
          <article className="card agent-row-card" key={agent.agent_id}>
            <div className="agent-row-main">
              <div className="agent-avatar">{getAgentInitials(agent.agent_name)}</div>
              <div>
                <div className="agent-row-title">
                  <Link to={`/agents/${agent.agent_slug}`}>{agent.agent_name}</Link>
                </div>
                <div className="muted">{agent.description}</div>
              </div>
            </div>
            <div className="agent-row-meta">
              <div className="inline-stat">
                <span>Status</span>
                <strong>
                  <span className="badge">{getStatusLabel(agent.status)}</span>
                </strong>
              </div>
              <div className="inline-stat">
                <span>Last Run</span>
                <strong>{agent.last_run_at}</strong>
              </div>
              <div className="inline-stat">
                <span>Runs 7d</span>
                <strong>{agent.run_count_7d}</strong>
              </div>
              <div className="inline-stat">
                <span>Cost 30d</span>
                <strong>{formatMoney(agent.cost_30d)}</strong>
              </div>
            </div>
          </article>
        ))}
      </section>
    </>
  );
}

function AgentDetailPage() {
  const { agentSlug = "" } = useParams();
  const state = useAsyncData(() => fetchAgentDetail(agentSlug), [agentSlug]);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const { agent, latest_run: latestRun, cost_total: costTotal, artifacts } = state.data;

  return (
    <>
      <section className="detail-hero">
        <article className="card detail-hero-main">
          <div className="section-kicker">Agent Overview</div>
          <h2>{agent.agent_name}</h2>
          <p>{agent.description}</p>
          <div className="detail-stats">
            <div className="mini-stat">
              <div className="mini-label">Status</div>
              <div className="mini-value">
                <span className="badge">{getStatusLabel(agent.status)}</span>
              </div>
            </div>
            <div className="mini-stat">
              <div className="mini-label">Last Run</div>
              <div className="mini-value">{agent.last_run_at}</div>
            </div>
            <div className="mini-stat">
              <div className="mini-label">Cost 30d</div>
              <div className="mini-value">{formatMoney(costTotal)}</div>
            </div>
            <div className="mini-stat">
              <div className="mini-label">Runs 7d</div>
              <div className="mini-value">{agent.run_count_7d}</div>
            </div>
          </div>
          <div className="button-row">
            <Link className="button" to={`/agents/${agent.agent_slug}/runs`}>
              View Run History
            </Link>
            <button className="button button-secondary" type="button">
              Run Now
            </button>
          </div>
        </article>
        <article className="card detail-side-card">
          <div className="card-head">
            <h2>Recent Run</h2>
            <span className="subtle-tag">Live</span>
          </div>
          {latestRun ? (
            <div className="stack-list">
              <div>
                <strong>Run ID</strong>
                <div>
                  <Link to={`/runs/${latestRun.run_id}`}>{latestRun.run_id}</Link>
                </div>
              </div>
              <div>
                <strong>Status</strong>
                <div>
                  <span className="badge">{getStatusLabel(latestRun.status)}</span>
                </div>
              </div>
              <div>
                <strong>Trigger</strong>
                <div>{getTriggerLabel(latestRun.trigger_type)}</div>
              </div>
            </div>
          ) : (
            <p className="muted">실행 이력이 없습니다.</p>
          )}
        </article>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Latest Artifacts</h2>
          <span className="subtle-tag">Latest Output</span>
        </div>
        <ul className="artifact-list">
          {artifacts.length > 0 ? (
            artifacts.map((artifact) => (
              <ArtifactItem artifact={artifact} key={`${artifact.artifact_name}-${artifact.version}`} />
            ))
          ) : (
            <li className="muted">산출물이 없습니다.</li>
          )}
        </ul>
      </section>
    </>
  );
}

function AgentRunsPage() {
  const { agentSlug = "" } = useParams();
  const state = useAsyncData(
    async () => {
      const [agentDetail, runs] = await Promise.all([fetchAgentDetail(agentSlug), fetchAgentRuns(agentSlug)]);
      return { agent: agentDetail.agent, runs: runs.runs };
    },
    [agentSlug],
  );

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <section className="section-intro">
        <div>
          <div className="section-kicker">Run History</div>
          <h2>{state.data.agent.agent_name} Run History</h2>
          <p className="muted">에이전트별 실행 결과, 상태, 비용, 소요 시간을 한눈에 확인합니다.</p>
        </div>
      </section>

      <section className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Started At</th>
              <th>Duration</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {state.data.runs.map((run) => (
              <tr key={run.run_id}>
                <td>
                  <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                </td>
                <td>
                  <span className="badge">{getStatusLabel(run.status)}</span>
                </td>
                <td>{getTriggerLabel(run.trigger_type)}</td>
                <td>{run.started_at}</td>
                <td>{formatDuration(run.total_duration_ms)}</td>
                <td>{formatMoney(run.total_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function RunDetailPage() {
  const { runId = "" } = useParams();
  const state = useAsyncData(() => fetchRunDetail(runId), [runId]);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const { run, logs, artifacts } = state.data;

  return (
    <>
      <section className="detail-hero">
        <article className="card detail-hero-main">
          <div className="section-kicker">Run Summary</div>
          <h2>Run Summary</h2>
          <ul className="stack-list">
            <li>
              <strong>Run ID:</strong> {run.run_id}
            </li>
            <li>
              <strong>Agent:</strong> {run.agent_name}
            </li>
            <li>
              <strong>Status:</strong> <span className="badge">{getStatusLabel(run.status)}</span>
            </li>
            <li>
              <strong>Started At:</strong> {run.started_at}
            </li>
            <li>
              <strong>Ended At:</strong> {run.ended_at ?? "-"}
            </li>
            <li>
              <strong>Cost:</strong> {formatMoney(run.total_cost)}
            </li>
          </ul>
        </article>
        <article className="card detail-side-card">
          <div className="card-head">
            <h2>Error Info</h2>
            <span className="subtle-tag">Debug</span>
          </div>
          <ul className="stack-list">
            <li>
              <strong>Stage:</strong> {run.error_stage ?? "-"}
            </li>
            <li>
              <strong>Type:</strong> {run.error_type ?? "-"}
            </li>
            <li>
              <strong>Message:</strong> {run.error_message ?? "-"}
            </li>
          </ul>
        </article>
      </section>

      <section className="two-column">
        <article className="card">
          <div className="card-head">
            <h2>Logs</h2>
            <span className="subtle-tag">Timeline</span>
          </div>
          <ul className="timeline-list">
            {logs.map((log) => (
              <li key={`${log.stage}-${log.status}`}>
                <span className="timeline-dot" />
                <div>
                  <div className="timeline-head">
                    <strong>{log.stage}</strong>
                    <span className="badge">{getStatusLabel(log.status)}</span>
                  </div>
                  <div className="muted">{log.message}</div>
                </div>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <div className="card-head">
            <h2>Artifacts</h2>
            <span className="subtle-tag">Artifacts</span>
          </div>
          <ul className="artifact-list">
            {artifacts.length > 0 ? (
              artifacts.map((artifact) => (
                <ArtifactItem artifact={artifact} key={`${artifact.artifact_name}-${artifact.version}`} />
              ))
            ) : (
              <li className="muted">산출물이 없습니다.</li>
            )}
          </ul>
        </article>
      </section>
    </>
  );
}

function CostsPage() {
  const state = useAsyncData(fetchCosts, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const data = state.data;

  return (
    <>
      <section className="card-grid">
        {data.summary_cards.map((card) => (
          <article className="card summary-card" key={card.label}>
            <div>
              <div className="card-label">{card.label}</div>
              <div className="card-value">{card.value}</div>
            </div>
          </article>
        ))}
      </section>

      <section className="section-intro">
        <div>
          <div className="section-kicker">Cost Overview</div>
          <h2>Cost Analytics</h2>
          <p className="muted">Provider별 비용과 에이전트별 비용 흐름을 기준으로 사용량을 추적합니다.</p>
        </div>
      </section>

      <section className="two-column">
        <article className="card">
          <h2>Provider Cost</h2>
          <ul className="metric-list">
            {Object.entries(data.by_provider).map(([provider, amount]) => (
              <li key={provider}>
                <span>{provider}</span>
                <strong>{formatMoney(amount)}</strong>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <h2>Agent Cost</h2>
          <ul className="metric-list">
            {Object.entries(data.by_agent).map(([agentSlug, amount]) => (
              <li key={agentSlug}>
                <span>{agentSlug}</span>
                <strong>{formatMoney(amount)}</strong>
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Cost Records</h2>
          <span className="subtle-tag">Raw Records</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Agent</th>
              <th>Provider</th>
              <th>Service</th>
              <th>Usage</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={`${row.recorded_at}-${row.provider}-${row.service_type}`}>
                <td>{row.recorded_at}</td>
                <td>{row.agent_slug}</td>
                <td>{row.provider}</td>
                <td>{row.service_type}</td>
                <td>
                  {row.usage_amount} {row.usage_unit}
                </td>
                <td>{formatMoney(row.cost_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function SettingsPage() {
  const state = useAsyncData(fetchSettings, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <section className="section-intro">
        <div>
          <div className="section-kicker">Platform Settings</div>
          <h2>Settings</h2>
          <p className="muted">플랫폼 기본 설정과 다음 연결 작업을 한곳에서 관리합니다.</p>
        </div>
      </section>

      <section className="two-column">
        <article className="card">
          <h2>Platform Settings</h2>
          <ul className="settings-list">
            {Object.entries(state.data.settings).map(([key, value]) => (
              <li key={key}>
                <span>{key}</span>
                <strong>{value}</strong>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <h2>Next Steps</h2>
          <ul className="check-list">
            <li>OpenAI API Key 연결</li>
            <li>Image Provider 설정</li>
            <li>Google API 인증 정보 설정</li>
            <li>Instagram API 인증 정보 설정</li>
          </ul>
        </article>
      </section>
    </>
  );
}

function ArtifactItem({ artifact }: { artifact: Artifact }) {
  return (
    <li>
      <div>
        <strong>{artifact.artifact_name}</strong>
        <div className="muted">
          {artifact.artifact_type} v{artifact.version}
        </div>
      </div>
      <a className="artifact-link" href={artifact.file_url}>
        Open
      </a>
    </li>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

export default App;
